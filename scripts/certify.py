#!/usr/bin/env python3
"""
certify.py

Post-installation certification report for Itential Automation Platform.
Connects to one or more IAP nodes, collects health and status information
from the platform APIs, optionally collects Kubernetes cluster state, and
writes a markdown report suitable for sharing with customers or archiving
as installation documentation.

Requirements:
  Python 3.8+. No third-party packages required.

Authentication:
  Three modes are supported (evaluated in priority order):

  1. Default (admin/admin)
       No flags needed. Tries admin/admin on first connect. If rejected,
       prompts securely for the correct password.

  2. Username/password
       --username <name>
       Prompts securely for the password. Never pass passwords as flags.

  3. Pre-supplied session token (SSO)
       --token <value>
       Skips /login entirely. Obtain the token from the browser:
       DevTools > Application > Cookies > token. Tokens expire after the
       session TTL (default 60 min) so run the script immediately after copying.

  4. OAuth2 client credentials
       --client-id <id>
       Prompts securely for the client secret. Uses the Authorization: Bearer
       header for all API calls instead of a session cookie.

Usage:
  python3 certify.py
  python3 certify.py --host https://iap.example.com
  python3 certify.py --host https://iap01.example.com --host https://iap02.example.com
  python3 certify.py --host https://iap.example.com --username operator
  python3 certify.py --host https://iap.example.com --token <session-token>
  python3 certify.py --host https://iap.example.com --client-id <id>
  python3 certify.py --host https://iap.example.com --ca-cert /path/to/ca.crt
  python3 certify.py --host https://iap.example.com -n <namespace>

If --host is not provided the script prompts for a URL interactively.

Kubernetes:
  If kubectl is available on PATH, the script collects pods, services,
  ingress, PVCs, configmaps, nodes, events, and pod resource usage from
  the specified namespace and includes them in the report.
  Use -n / --namespace to target a specific namespace. Defaults to the
  active kubectl context namespace.

Output:
  iap-certify-{hostname}-{YYYY-MM-DD}.md written to the current directory.
  If the file cannot be written the report is printed to stdout instead.
"""

import argparse
import getpass
import json
import shutil
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

_DEFAULT_USER = "admin"
_DEFAULT_PASS = "admin"

# Underscore-delimited tokens that indicate a config value should be redacted.
# Matching is done on whole tokens (split by "_") to avoid false positives like
# "bypass" matching "pass".
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "credential", "apikey", "privatekey",
})


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    """
    Parse and return command-line arguments.
    argparse handles validation and exits with a usage message on bad input,
    so no additional error handling is needed here.
    """
    p = argparse.ArgumentParser(
        description="Collect IAP health data and produce a markdown certification report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--host", dest="hosts", action="append", metavar="URL",
        help="IAP base URL (e.g. https://iap.example.com). May be repeated for multiple nodes. "
             "Prompted interactively when omitted.",
    )
    p.add_argument(
        "--username", default=_DEFAULT_USER, metavar="NAME",
        help=f"IAP username. Defaults to '{_DEFAULT_USER}' using the default password. "
             "When any other username is provided the password is prompted securely.",
    )
    p.add_argument(
        "--token", metavar="VALUE",
        help="Session token to use directly, skipping login. Use this for SSO-protected instances: "
             "log in via browser, copy the 'token' cookie value from DevTools, and pass it here.",
    )
    p.add_argument(
        "--client-id", metavar="ID",
        help="OAuth2 client ID for client_credentials login. "
             "The client secret is always prompted securely — never pass it on the command line.",
    )
    p.add_argument(
        "--namespace", "-n", metavar="NS",
        help="Kubernetes namespace to query for cluster resources. "
             "Defaults to the current kubectl context namespace when kubectl is available.",
    )
    p.add_argument(
        "--ca-cert", metavar="PATH",
        help="Path to a CA certificate file for TLS verification. "
             "TLS verification is skipped when this option is omitted.",
    )
    return p.parse_args()


def _hostname(url):
    """
    Extract the bare hostname from a URL, stripping protocol and port.
    Falls back to the raw url string if urlparse cannot identify a hostname
    (e.g. if the user passed a bare IP without a scheme).
    """
    try:
        hostname = urllib.parse.urlparse(url).hostname
        return hostname if hostname else url
    except Exception:
        return url


# ── HTTP / Auth ───────────────────────────────────────────────────────────────

def _ssl_ctx(ca_cert=None):
    """
    Build an SSL context for HTTPS connections.

    When ca_cert is provided the server certificate is verified against that CA,
    which is the secure option for known deployments.  When omitted, verification
    is disabled — necessary for self-signed IAP certs that are common in customer
    environments.  Exits immediately with a clear message if the CA file cannot
    be loaded rather than proceeding with a broken context.
    """
    ctx = ssl.create_default_context()
    if ca_cert:
        try:
            ctx.load_verify_locations(cafile=ca_cert)
        except FileNotFoundError:
            sys.exit(
                f"CA certificate file not found: '{ca_cert}'\n"
                "Check the path and try again."
            )
        except ssl.SSLError as exc:
            sys.exit(
                f"Failed to load CA certificate '{ca_cert}': {exc}\n"
                "Ensure the file is a valid PEM-encoded certificate."
            )
        except OSError as exc:
            sys.exit(
                f"Cannot read CA certificate '{ca_cert}': {exc}"
            )
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _login(base, username, password, ctx):
    """
    Authenticate to IAP and return the session token string.

    POSTs credentials to /login and extracts the token from either the JSON
    response body or the Set-Cookie response headers.  Multiple Set-Cookie
    headers are checked because ALB and other load-balancers inject their own
    cookies before IAP's token cookie, which means checking only the first
    header misses the token.

    Returns None on any failure — the caller is responsible for deciding how to
    handle a missing token (prompt, retry, report failure).
    """
    url = f"{base.rstrip('/')}/login"
    try:
        payload = json.dumps({"user": {"username": username, "password": password}}).encode()
    except (TypeError, ValueError) as exc:
        print(f"    [login] Failed to build request payload: {exc}", file=sys.stderr)
        return None

    req = urllib.request.Request(
        url, method="POST", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            if resp.status != 200:
                print(
                    f"    [login] Server returned HTTP {resp.status} for POST {url} — "
                    "credentials may be wrong or the login endpoint is unavailable.",
                    file=sys.stderr,
                )
                return None

            body = resp.read().decode("utf-8", errors="replace")

            # Attempt 1: token in JSON body
            try:
                token = json.loads(body).get("token")
                if token:
                    return token
            except (json.JSONDecodeError, AttributeError):
                pass

            # Attempt 2: scan every Set-Cookie header (ALB/proxies add extra ones
            # before IAP's token cookie, so getheader() alone is not sufficient)
            for cookie_header in (resp.headers.get_all("Set-Cookie") or []):
                for part in cookie_header.split(";"):
                    part = part.strip()
                    if part.lower().startswith("token="):
                        return part.split("=", 1)[1]

            print(
                f"    [login] POST {url} returned HTTP 200 but no token was found "
                "in the response body or Set-Cookie headers.  The IAP instance may "
                "use a non-standard authentication flow.",
                file=sys.stderr,
            )
            return None

    except urllib.error.HTTPError as exc:
        print(
            f"    [login] HTTP {exc.code} from POST {url} — "
            f"{exc.reason or 'no detail available'}.",
            file=sys.stderr,
        )
    except urllib.error.URLError as exc:
        print(
            f"    [login] Cannot reach {url}: {exc.reason}  "
            "Check that the host is correct and the IAP service is running.",
            file=sys.stderr,
        )
    except ssl.SSLError as exc:
        print(
            f"    [login] TLS error connecting to {url}: {exc}  "
            "Use --ca-cert if the server uses a private CA.",
            file=sys.stderr,
        )
    except OSError as exc:
        print(
            f"    [login] Network error on POST {url}: {exc}",
            file=sys.stderr,
        )
    return None


def _login_oauth(base, client_id, client_secret, ctx):
    """
    Authenticate to IAP using the OAuth2 client_credentials flow.

    POSTs form-encoded credentials to /oauth/token and returns the access_token
    string on success.  Subsequent requests must send this token as an
    Authorization: Bearer header rather than a cookie — callers are responsible
    for setting bearer=True when calling _get.

    Returns None on any failure with a descriptive message printed to stderr.
    """
    url = f"{base.rstrip('/')}/oauth/token"
    try:
        body = urllib.parse.urlencode({
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "client_credentials",
        }).encode()
    except Exception as exc:
        print(f"    [oauth] Failed to build request body: {exc}", file=sys.stderr)
        return None

    req = urllib.request.Request(
        url, method="POST", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            if resp.status != 200:
                print(
                    f"    [oauth] Server returned HTTP {resp.status} for POST {url} — "
                    "check that the client ID and secret are correct.",
                    file=sys.stderr,
                )
                return None
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            token = data.get("access_token")
            if not token:
                print(
                    f"    [oauth] POST {url} returned HTTP 200 but no access_token "
                    "was found in the response.  Response keys: "
                    f"{list(data.keys()) if isinstance(data, dict) else type(data).__name__}",
                    file=sys.stderr,
                )
            return token
    except urllib.error.HTTPError as exc:
        print(
            f"    [oauth] HTTP {exc.code} from POST {url} — "
            f"{exc.reason or 'no detail available'}.",
            file=sys.stderr,
        )
    except urllib.error.URLError as exc:
        print(
            f"    [oauth] Cannot reach {url}: {exc.reason}  "
            "Check that the host is correct and the IAP service is running.",
            file=sys.stderr,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(
            f"    [oauth] Could not parse response from {url}: {exc}",
            file=sys.stderr,
        )
    except ssl.SSLError as exc:
        print(
            f"    [oauth] TLS error connecting to {url}: {exc}  "
            "Use --ca-cert if the server uses a private CA.",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"    [oauth] Network error on POST {url}: {exc}", file=sys.stderr)
    return None


def _get(base, path, token, ctx, bearer=False):
    """
    Perform an authenticated GET request against an IAP endpoint.

    When bearer=False (default) the token is sent as a session cookie, which
    is the standard IAP username/password and SSO flow.  When bearer=True the
    token is sent as an Authorization: Bearer header, which is required for
    the OAuth2 client_credentials flow.

    Returns the parsed JSON response on
    success.  On any failure returns a dict containing '_error' with a
    description that includes the URL and failure reason so it can be rendered
    into the report instead of crashing the script.
    """
    url = f"{base.rstrip('/')}{path}"
    headers = {"Authorization": f"Bearer {token}"} if bearer else {"Cookie": f"token={token}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                return {"_error": f"HTTP {resp.status} from GET {url}"}
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                return {
                    "_error": f"Invalid JSON from GET {url}: {exc}",
                    "_raw": body[:200],
                }
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code} from GET {url} — {exc.reason or 'no detail'}"}
    except urllib.error.URLError as exc:
        return {"_error": f"Cannot reach {url}: {exc.reason}"}
    except ssl.SSLError as exc:
        return {"_error": f"TLS error on GET {url}: {exc}"}
    except OSError as exc:
        return {"_error": f"Network error on GET {url}: {exc}"}


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_bytes(n):
    """
    Format a byte count as a human-readable string (e.g. 189.7 MB).
    Returns '—' for any non-numeric input rather than raising.
    """
    try:
        if not isinstance(n, (int, float)):
            return "—"
        for unit in ("B", "KB", "MB", "GB"):
            if abs(n) < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
    except Exception:
        return "—"


def _fmt_uptime(sec):
    """
    Format a seconds value as a human-readable uptime string (e.g. '4h 12m').
    Returns '—' for zero, negative, or non-numeric input rather than raising.
    """
    try:
        if not isinstance(sec, (int, float)) or sec <= 0:
            return "—"
        h, rem = divmod(int(sec), 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
    except Exception:
        return "—"


def _fmt_ts(ms):
    """
    Convert a Unix timestamp in milliseconds to a UTC datetime string.
    Returns '—' for any non-numeric or out-of-range input rather than raising.
    """
    try:
        if not isinstance(ms, (int, float)):
            return "—"
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    except (OSError, OverflowError, ValueError):
        return str(ms)
    except Exception:
        return "—"


def _redact(obj, _depth=0):
    """
    Recursively walk a JSON-decoded structure and replace values whose key
    contains a sensitive token (matched on whole underscore-delimited parts)
    with the string '[REDACTED]'.  The depth limit prevents runaway recursion
    on pathologically nested structures returned by some endpoints.
    """
    try:
        if _depth > 15:
            return obj
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if any(s in k.lower() for s in _SENSITIVE_KEYS)
                else _redact(v, _depth + 1)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_redact(i, _depth + 1) for i in obj]
        return obj
    except Exception:
        return obj


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _table(headers, rows):
    """
    Build a GitHub-Flavored Markdown table string from a list of headers and rows.
    Pipe characters inside cell content are escaped so they do not break the table.
    Rows with fewer cells than headers are padded; extra cells are silently dropped.
    """
    try:
        ncols = len(headers)
        lines = [
            "| " + " | ".join(str(h) for h in headers) + " |",
            "|" + "|".join("---" for _ in range(ncols)) + "|",
        ]
        for row in rows:
            cells = [str(c).replace("|", "\\|") for c in list(row)[:ncols]]
            while len(cells) < ncols:
                cells.append("—")
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)
    except Exception as exc:
        return f"_Table render error: {exc}_"


def _safe_render(name, func, data):
    """
    Call a render function and catch any exception it raises.
    Returns an error blockquote instead of crashing the script if the data
    returned by an API endpoint has an unexpected shape.
    """
    try:
        return func(data)
    except Exception as exc:
        return (
            f"> **Render error in '{name}':** `{exc}`  \n"
            f"> The raw data shape was unexpected. "
            f"Data type received: `{type(data).__name__}`.\n\n"
        )


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_health_status(d):
    """
    Render the /health/status response as a markdown field/value table.
    The services array (Redis, MongoDB, Vault, etc.) is expanded into individual
    rows so their status is immediately visible without reading raw JSON.
    """
    if not isinstance(d, dict):
        return f"> **Unexpected response shape:** received `{type(d).__name__}`, expected dict.\n\n"
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    rows = [
        ("Host",       f"`{d.get('host', '—')}`"),
        ("Server ID",  f"`{d.get('serverId', '—')}`"),
        ("Timestamp",  _fmt_ts(d.get("timestamp"))),
        ("Apps",       f"`{d.get('apps', '—')}`"),
        ("Adapters",   f"`{d.get('adapters', '—')}`"),
    ]
    for svc in (d.get("services") or []):
        if isinstance(svc, dict):
            label = str(svc.get("service", "service")).capitalize()
            rows.append((label, f"`{svc.get('status', '—')}`"))
    return _table(["Field", "Value"], rows) + "\n\n"


def _render_server(d):
    """
    Render the /health/server response showing version, runtime, and memory info.
    The core dependencies dict is flattened into a single row for readability.
    """
    if not isinstance(d, dict):
        return f"> **Unexpected response shape:** received `{type(d).__name__}`, expected dict.\n\n"
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    mem  = d.get("memoryUsage") or {}
    deps = d.get("dependencies") or {}
    rows = [
        ("Version",           f"`{d.get('version', '—')}`"),
        ("Release",           f"`{d.get('release', '—')}`"),
        ("Build",             f"`{d.get('build', '—')}`"),
        ("Platform",          f"`{d.get('platform', '—')}` / `{d.get('arch', '—')}`"),
        ("Node.js",           f"`{(d.get('versions') or {}).get('node', '—')}`"),
        ("PID",               f"`{d.get('pid', '—')}`"),
        ("Uptime",            _fmt_uptime(d.get("uptime"))),
        ("RSS",               _fmt_bytes(mem.get("rss", 0))),
        ("Heap used / total", f"{_fmt_bytes(mem.get('heapUsed', 0))} / {_fmt_bytes(mem.get('heapTotal', 0))}"),
    ]
    if isinstance(deps, dict) and deps:
        rows.append(("Core deps", ", ".join(f"`{k}@{v}`" for k, v in deps.items())))
    return _table(["Field", "Value"], rows) + "\n\n"


def _render_adapters(d):
    """
    Render the /health/adapters response as a table sorted by state then ID.
    DEAD and STOPPED adapters sort before RUNNING so problems are visible at
    the top without scrolling.
    """
    if not isinstance(d, dict):
        return f"> **Unexpected response shape:** received `{type(d).__name__}`, expected dict.\n\n"
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    results = d.get("results") or []
    if not results:
        return "_No adapters found._\n\n"
    rows = []
    for a in sorted(results, key=lambda x: (x.get("state", "") if isinstance(x, dict) else "", x.get("id", "") if isinstance(x, dict) else "")):
        if not isinstance(a, dict):
            continue
        conn = (a.get("connection") or {}).get("state", "—")
        rss  = _fmt_bytes((a.get("memoryUsage") or {}).get("rss", 0))
        rows.append([
            f"`{a.get('id', '')}`",
            f"`{a.get('package_id', '')}`",
            f"`{a.get('version', '')}`",
            a.get("state", "—"),
            conn,
            _fmt_uptime(a.get("uptime", 0)),
            rss,
        ])
    total = d.get("total", len(results))
    return (
        _table(["ID", "Package", "Version", "State", "Connection", "Uptime", "RSS"], rows)
        + f"\n\n**Total: {total}**\n\n"
    )


def _render_applications(d):
    """
    Render the /health/applications response as a table sorted alphabetically by ID.
    """
    if not isinstance(d, dict):
        return f"> **Unexpected response shape:** received `{type(d).__name__}`, expected dict.\n\n"
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    results = d.get("results") or []
    if not results:
        return "_No applications found._\n\n"
    rows = []
    for a in sorted(results, key=lambda x: x.get("id", "") if isinstance(x, dict) else ""):
        if not isinstance(a, dict):
            continue
        rss = _fmt_bytes((a.get("memoryUsage") or {}).get("rss", 0))
        rows.append([
            f"`{a.get('id', '')}`",
            f"`{a.get('package_id', '')}`",
            f"`{a.get('version', '')}`",
            a.get("state", "—"),
            _fmt_uptime(a.get("uptime", 0)),
            rss,
        ])
    total = d.get("total", len(results))
    return (
        _table(["ID", "Package", "Version", "State", "Uptime", "RSS"], rows)
        + f"\n\n**Total: {total}**\n\n"
    )


def _render_integrations(d):
    """
    Render the /integration-models response.
    The response uses an 'integrationModels' key (not 'results') and includes
    server connection details nested under 'properties.server'.  Each row shows
    the model name, the configured host, and a truncated description.
    """
    if "_error" in (d if isinstance(d, dict) else {}):
        return f"> **Error:** {d['_error']}\n\n"
    if isinstance(d, list):
        results, total = d, len(d)
    elif isinstance(d, dict):
        results = d.get("integrationModels", d.get("results", d.get("items", []))) or []
        total   = d.get("total", len(results))
    else:
        return f"> **Unexpected response shape:** received `{type(d).__name__}`.\n\n"
    if not results:
        return "_No integration models found._\n\n"
    rows = []
    for m in results:
        if not isinstance(m, dict):
            continue
        name  = m.get("versionId", m.get("model", "—"))
        desc  = str(m.get("description") or "").replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:87] + "..."
        props  = m.get("properties") or {}
        server = props.get("server") or {} if isinstance(props, dict) else {}
        proto  = server.get("protocol", "")
        host   = server.get("host", "")
        endpoint = f"{proto}://{host}".strip(":/") if (proto or host) else "—"
        rows.append([f"`{name}`", endpoint, desc])
    return (
        _table(["Model", "Host", "Description"], rows)
        + f"\n\n**Total: {total}**\n\n"
    )


def _render_config(d):
    """
    Render the /server/config response.
    The endpoint returns a flat array of {name, origin, value} entries.  Values
    that IAP already masks with '********' or whose name contains a sensitive
    key token are shown as [REDACTED].  Unexpected response shapes fall back to
    a redacted JSON code block.
    """
    if isinstance(d, dict) and "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    if isinstance(d, list) and d and isinstance(d[0], dict) and "name" in d[0] and "origin" in d[0]:
        rows = []
        for entry in d:
            if not isinstance(entry, dict):
                continue
            name   = str(entry.get("name", ""))
            origin = str(entry.get("origin", ""))
            value  = entry.get("value", "")
            name_tokens = set(name.lower().split("_"))
            if str(value) == "********" or name_tokens & _SENSITIVE_KEYS:
                display = "`[REDACTED]`"
            else:
                display = f"`{value}`" if value != "" else "—"
            rows.append([f"`{name}`", origin, display])
        return _table(["Name", "Origin", "Value"], rows) + "\n\n"
    # Fallback: unknown shape — redact and dump as JSON
    try:
        return "```json\n" + json.dumps(_redact(d), indent=2) + "\n```\n\n"
    except Exception as exc:
        return f"> **Could not render server config:** {exc}\n\n"


def _render_workers(d):
    """
    Render the /workflow_engine/workers/status response showing job and task
    worker state.  Columns map directly to the admin UI labels:
      running       → Accept New Jobs / Execute Job Tasks toggle
      clusterValue  → Enabled Centrally (Default)
      localValue    → Enabled Locally
      startupValue  → value at startup
    """
    if not isinstance(d, dict):
        return f"> **Unexpected response shape:** received `{type(d).__name__}`, expected dict.\n\n"
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    rows = []
    for label, key in [("Job Worker", "jobWorker"), ("Task Worker", "taskWorker")]:
        w = d.get(key) or {}
        if not isinstance(w, dict):
            w = {}
        rows.append([
            label,
            "Yes" if w.get("running") else "No",
            str(w.get("clusterValue", "—")),
            str(w.get("localValue", "—")),
            str(w.get("startupValue", "—")),
        ])
    return _table(["Worker", "Running", "Cluster Value (Central)", "Local Value", "Startup Value"], rows) + "\n\n"


# ── Kubernetes ────────────────────────────────────────────────────────────────

# Each entry: (display label, kubectl args, namespaced)
# namespaced=True  → -n <namespace> is appended
# namespaced=False → cluster-level resource, no namespace flag
_K8S_CHECKS = [
    ("Pods",                     ["get", "pods", "-o", "wide"],                 True),
    ("StatefulSets",             ["get", "statefulsets"],                       True),
    ("Services",                 ["get", "services"],                           True),
    ("Ingress",                  ["get", "ingress"],                            True),
    ("Persistent Volume Claims", ["get", "pvc"],                                True),
    ("ConfigMaps",               ["get", "configmaps"],                         True),
    ("Nodes",                    ["get", "nodes"],                              False),
    ("Pod Resource Usage",       ["top", "pods"],                               True),
    ("Events",                   ["get", "events", "--sort-by=.lastTimestamp"], True),
]


def _kubectl_available():
    """
    Return True if kubectl is present on PATH, False otherwise.
    Uses shutil.which so it works on all platforms without spawning a process.
    """
    return shutil.which("kubectl") is not None


def _kubectl_run(args, namespace=None):
    """
    Execute a kubectl command and return its stdout as a string.

    Returns None when the command fails, the resource type does not exist in
    the cluster, or the process cannot be started.  The full command and any
    stderr output are printed to stderr so the operator can diagnose failures
    without having to re-run kubectl manually.
    """
    cmd = ["kubectl"] + args + (["-n", namespace] if namespace else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip() or None
        # Non-zero exit — print stderr so the operator can see why it failed
        detail = r.stderr.strip() or "no error detail available"
        print(
            f"  [kubectl] Command failed (exit {r.returncode}): {' '.join(cmd)}\n"
            f"            {detail}",
            file=sys.stderr,
        )
        return None
    except subprocess.TimeoutExpired:
        print(
            f"  [kubectl] Timed out after 30s: {' '.join(cmd)}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        # kubectl disappeared between the availability check and execution
        print(
            "  [kubectl] kubectl not found — was it removed during the run?",
            file=sys.stderr,
        )
        return None
    except OSError as exc:
        print(
            f"  [kubectl] OS error running {' '.join(cmd)}: {exc}",
            file=sys.stderr,
        )
        return None


def _kubectl_context_namespace():
    """
    Return the namespace set in the active kubectl context.
    Falls back to 'default' if the context has no namespace set or if the
    kubectl config command fails for any reason.
    """
    try:
        r = subprocess.run(
            ["kubectl", "config", "view", "--minify", "-o",
             "jsonpath={.contexts[0].context.namespace}"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "default"
    except subprocess.TimeoutExpired:
        print(
            "  [kubectl] Timed out reading current context namespace — using 'default'.",
            file=sys.stderr,
        )
        return "default"
    except (FileNotFoundError, OSError) as exc:
        print(
            f"  [kubectl] Cannot read context namespace: {exc} — using 'default'.",
            file=sys.stderr,
        )
        return "default"


def _collect_k8s(namespace):
    """
    Run all kubectl checks in _K8S_CHECKS and return their output in a dict.
    Each value is either the command's stdout string or None if the command
    failed or produced no output (e.g. no pods exist, metrics unavailable).
    The namespace key is always present for use in the report header.
    """
    data = {"namespace": namespace}
    for label, args, namespaced in _K8S_CHECKS:
        try:
            data[label] = _kubectl_run(args, namespace=namespace if namespaced else None)
        except Exception as exc:
            print(
                f"  [kubectl] Unexpected error collecting '{label}': {exc}",
                file=sys.stderr,
            )
            data[label] = None
    return data


def _render_k8s(data):
    """
    Render the collected kubectl outputs as a markdown section.
    Each check gets its own subsection with the raw kubectl output in a code
    block.  Checks that returned None are shown as 'Not available' — this is
    normal for resources that do not exist in the namespace or for kubectl top
    when the metrics server is not installed.
    """
    if not isinstance(data, dict):
        return "> **Kubernetes data unavailable.**\n\n"
    namespace = data.get("namespace", "unknown")
    out = [f"**Namespace:** `{namespace}`\n\n"]
    for label, _, _ in _K8S_CHECKS:
        out.append(f"#### {label}\n\n")
        output = data.get(label)
        if output:
            out.append(f"```\n{output}\n```\n\n")
        else:
            out.append("_Not available._\n\n")
    return "".join(out)


# ── Data collection ───────────────────────────────────────────────────────────

_ENDPOINTS = [
    ("health_status",   "/health/status"),
    ("health_server",   "/health/server"),
    ("health_adapters", "/health/adapters"),
    ("health_apps",     "/health/applications"),
    ("integrations",    "/integration-models"),
    ("worker_status",   "/workflow_engine/workers/status"),
    ("server_config",   "/server/config"),
]


def _collect(base, username, password, ctx, token=None, bearer=False):
    """
    Authenticate to an IAP node and fetch all endpoint data.

    Three auth modes are supported:
      token + bearer=False  → pre-supplied SSO session cookie
      token + bearer=True   → pre-supplied OAuth2 Bearer token (client_credentials)
      token=None            → call _login with username/password to get a cookie token

    Before fetching all endpoints a lightweight probe against /health/server
    validates the token — a 401 at this stage means the token is expired or
    wrong and we return early with a specific reason code so the caller can
    print a helpful message.

    Returns a dict with 'ok': True and one key per _ENDPOINTS entry on
    success, or 'ok': False (with an optional '_reason' key) on failure.
    """
    if not token:
        token = _login(base, username, password, ctx)
    if not token:
        return {"ok": False}

    # Probe with an authenticated call before fetching everything.
    # If we get a 401 the token is invalid/expired regardless of source.
    probe = _get(base, "/health/server", token, ctx, bearer=bearer)
    if "_error" in probe and "401" in str(probe.get("_error", "")):
        return {"ok": False, "_reason": "token_expired"}

    result = {"ok": True}
    for key, path in _ENDPOINTS:
        try:
            result[key] = _get(base, path, token, ctx, bearer=bearer)
        except Exception as exc:
            result[key] = {"_error": f"Unexpected error fetching {path}: {exc}"}
    return result


# ── Report assembly ───────────────────────────────────────────────────────────

def _build_report(hosts, results, generated_at, k8s_data=None):
    """
    Assemble the full markdown certification report from collected data.

    Iterates hosts and their corresponding result dicts.  Each host section
    calls the appropriate renderer via _safe_render so that a malformed or
    unexpected API response for one section cannot prevent the rest of the
    report from being written.
    """
    try:
        arch = "High Availability" if len(hosts) > 1 else "Single Node"
        out  = []

        out.append("# Itential Automation Platform — Certification Report\n\n")
        meta_rows = [
            ["**Generated**",    generated_at],
            ["**Architecture**", arch],
            ["**Nodes**",        str(len(hosts))],
            ["**Hosts**",        ", ".join(f"`{h}`" for h in hosts)],
        ]
        out.append(_table(["", ""], meta_rows) + "\n\n---\n\n")

        if k8s_data:
            out.append("## Kubernetes Resources\n\n")
            out.append(_safe_render("Kubernetes", _render_k8s, k8s_data))
            out.append("---\n\n")

        for host, r in zip(hosts, results):
            out.append(f"## `{host}`\n\n")

            if not isinstance(r, dict) or not r.get("ok"):
                reason = (r or {}).get("_reason", "")
                if reason == "token_expired":
                    out.append(
                        "**Login:** token expired or invalid — "
                        "obtain a fresh session token and re-run.\n\n---\n\n"
                    )
                else:
                    out.append("**Login:** failed\n\n---\n\n")
                continue

            out.append("**Login:** ok\n\n")

            out.append("### Health Status\n\n")
            out.append(_safe_render("Health Status", _render_health_status, r.get("health_status", {})))

            out.append("### Server Info\n\n")
            out.append(_safe_render("Server Info", _render_server, r.get("health_server", {})))

            out.append("### Adapters\n\n")
            out.append(_safe_render("Adapters", _render_adapters, r.get("health_adapters", {})))

            out.append("### Applications\n\n")
            out.append(_safe_render("Applications", _render_applications, r.get("health_apps", {})))

            out.append("### Integration Models\n\n")
            out.append(_safe_render("Integration Models", _render_integrations, r.get("integrations", {})))

            out.append("### Worker Status\n\n")
            out.append(_safe_render("Worker Status", _render_workers, r.get("worker_status", {})))

            out.append("### Server Configuration\n\n")
            out.append(_safe_render("Server Configuration", _render_config, r.get("server_config", {})))

            out.append("---\n\n")

        return "".join(out)

    except Exception as exc:
        # Last-resort fallback — if report assembly itself fails, return a
        # minimal document so the file is still written with diagnostic info.
        return (
            "# IAP Certification Report — Assembly Error\n\n"
            f"> Report generation encountered an unexpected error: `{exc}`\n\n"
            f"> Generated at: {generated_at}\n\n"
            f"> Hosts attempted: {', '.join(hosts)}\n"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    """
    Main entry point — orchestrates argument parsing, authentication, data
    collection, and report writing.

    The script is designed to never raise an unhandled exception.  Every
    collection step degrades gracefully: a failed host is marked as unreachable
    in the report, a failed kubectl command shows 'Not available', and a failed
    file write falls back to printing the report to stdout so the output is
    never lost.
    """
    args = _parse_args()

    # Collect hosts — prompt interactively if none provided on the command line
    hosts = list(dict.fromkeys(args.hosts or []))
    if not hosts:
        try:
            url = input("IAP host URL (e.g. https://iap.example.com): ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nNo host provided.")
        if not url:
            sys.exit("Error: no host provided.")
        hosts = [url]

    ctx = _ssl_ctx(args.ca_cert)

    # Auth resolution (evaluated in priority order):
    #   --token           → pre-supplied session cookie, skips /login (SSO)
    #   --client-id       → OAuth2 client_credentials, prompts for secret, uses Bearer header
    #   --username admin  → try default admin/admin first, prompt on rejection
    #   --username other  → prompt for password immediately
    supplied_token = args.token or None
    client_id      = getattr(args, "client_id", None)
    bearer         = False

    if supplied_token:
        password = None
        prompted = True
        print("Using supplied session token (SSO mode).")
    elif client_id:
        try:
            client_secret = getpass.getpass(f"Client secret for '{client_id}': ")
        except (EOFError, KeyboardInterrupt):
            sys.exit("\nClient secret entry cancelled.")
        supplied_token = None   # resolved per-host via _login_oauth
        password       = client_secret
        prompted       = True
        bearer         = True
        print("Using OAuth2 client_credentials flow (Bearer token).")
    else:
        using_default_creds = (args.username == _DEFAULT_USER)
        if using_default_creds:
            password = _DEFAULT_PASS
            prompted = False
        else:
            try:
                password = getpass.getpass(f"Password for '{args.username}': ")
            except (EOFError, KeyboardInterrupt):
                sys.exit("\nPassword entry cancelled.")
            prompted = True

    print(f"\nCertifying {len(hosts)} node(s)...\n")

    all_results = []
    for host in hosts:
        print(f"  {host}")

        # For OAuth, obtain a fresh Bearer token per host via /oauth/token
        if client_id:
            oauth_token = _login_oauth(host, client_id, password, ctx)
            result = _collect(host, None, None, ctx, token=oauth_token, bearer=True) \
                     if oauth_token else {"ok": False}
        else:
            result = _collect(host, args.username, password, ctx, token=supplied_token, bearer=bearer)

        # If default admin/admin was rejected, prompt once and reuse the new
        # password for all remaining hosts in the list
        if not (result or {}).get("ok") and not prompted:
            print("    Default credentials rejected.")
            try:
                password = getpass.getpass(f"    Password for '{args.username}': ")
            except (EOFError, KeyboardInterrupt):
                print("    Password entry cancelled — marking host as unreachable.")
                all_results.append({"ok": False})
                continue
            prompted = True
            result = _collect(host, args.username, password, ctx)

        if not (result or {}).get("ok"):
            if (result or {}).get("_reason") == "token_expired":
                print("    Login: token expired or invalid — grab a fresh token and re-run.")
            else:
                print("    Login: failed")
        else:
            print("    Login: ok")
            for key, path in _ENDPOINTS:
                endpoint_data = result.get(key, {})
                status = "ok" if "_error" not in (endpoint_data or {}) else \
                         f"error ({(endpoint_data or {}).get('_error', 'unknown')})"
                print(f"    {path}: {status}")

        all_results.append(result or {"ok": False})

    # Kubernetes resource collection — entirely optional, skipped if kubectl
    # is not on PATH or if any individual command fails
    k8s_data = None
    if _kubectl_available():
        namespace = args.namespace or _kubectl_context_namespace()
        print(f"\nCollecting Kubernetes resources (namespace: {namespace})...")
        k8s_data = _collect_k8s(namespace)
        for label, _, _ in _K8S_CHECKS:
            status = "ok" if k8s_data.get(label) else "not available"
            print(f"  {label}: {status}")
    else:
        print("\nkubectl not found — skipping Kubernetes resource collection.")

    now          = datetime.now(tz=timezone.utc)
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    report       = _build_report(hosts, all_results, generated_at, k8s_data=k8s_data)

    hostname = _hostname(hosts[0])
    outfile  = f"iap-certify-{hostname}-{now.strftime('%Y-%m-%d')}.md"

    try:
        with open(outfile, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"\nReport written to: {outfile}")
    except OSError as exc:
        print(
            f"\nCannot write to '{outfile}': {exc}\n"
            "Printing report to stdout instead:\n",
            file=sys.stderr,
        )
        print(report)


if __name__ == "__main__":
    main()
