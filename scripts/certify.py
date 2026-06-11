#!/usr/bin/env python3
"""
certify.py

Post-installation certification report for Itential Automation Platform.
Connects to one or more IAP nodes, collects health and status data, and
writes a markdown report suitable for sharing with customers.

Requirements:
  Python 3.8+. No third-party packages required.

Usage:
  python3 certify.py
  python3 certify.py --host https://iap.example.com
  python3 certify.py --host https://iap01.example.com --host https://iap02.example.com
  python3 certify.py --host https://iap.example.com --username operator
  python3 certify.py --host https://iap.example.com --ca-cert /path/to/ca.crt

If --host is not provided, the script will prompt for a URL interactively.

Output:
  iap-certify-{hostname}-{YYYY-MM-DD}.md
"""

import argparse
import getpass
import json
import ssl
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

_DEFAULT_USER = "admin"
_DEFAULT_PASS = "admin"

_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "credential", "apikey", "privatekey",
})


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
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
    """Extract the bare hostname from a URL, stripping protocol and port."""
    return urllib.parse.urlparse(url).hostname or url


# ── HTTP / Auth ───────────────────────────────────────────────────────────────

def _ssl_ctx(ca_cert=None):
    ctx = ssl.create_default_context()
    if ca_cert:
        ctx.load_verify_locations(cafile=ca_cert)
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _login(base, username, password, ctx):
    """Authenticate to IAP. Returns the session token string, or None on failure."""
    url = base.rstrip("/") + "/login"
    payload = json.dumps({"user": {"username": username, "password": password}}).encode()
    req = urllib.request.Request(
        url, method="POST", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            if resp.status != 200:
                return None
            body = resp.read().decode("utf-8", errors="replace")
            try:
                token = json.loads(body).get("token")
                if token:
                    return token
            except (json.JSONDecodeError, AttributeError):
                pass
            # Check all Set-Cookie headers — ALB and other proxies inject their own
            # cookies before IAP's token cookie, so getheader() alone misses it
            for cookie_header in (resp.headers.get_all("Set-Cookie") or []):
                for part in cookie_header.split(";"):
                    part = part.strip()
                    if part.lower().startswith("token="):
                        return part.split("=", 1)[1]
    except (urllib.error.HTTPError, urllib.error.URLError, ssl.SSLError, OSError):
        pass
    return None


def _get(base, path, token, ctx):
    """
    Authenticated GET against an IAP endpoint.
    Returns parsed JSON on success, or a dict with '_error' on failure.
    """
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Cookie": f"token={token}"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                return {"_error": f"HTTP {resp.status}"}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"_raw": body}
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        return {"_error": str(exc)}


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_bytes(n):
    if not isinstance(n, (int, float)):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_uptime(sec):
    if not isinstance(sec, (int, float)) or sec <= 0:
        return "—"
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _fmt_ts(ms):
    if not isinstance(ms, (int, float)):
        return "—"
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OSError, OverflowError, ValueError):
        return str(ms)


def _redact(obj, _depth=0):
    """Recursively replace values whose key matches a sensitive pattern with [REDACTED]."""
    if _depth > 15:
        return obj
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if any(s in k.lower() for s in _SENSITIVE_KEYS) else _redact(v, _depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(i, _depth + 1) for i in obj]
    return obj


# ── Markdown helpers ──────────────────────────────────────────────────────────

def _table(headers, rows):
    """Build a GFM-compatible markdown table string."""
    ncols = len(headers)
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "|" + "|".join("---" for _ in range(ncols)) + "|",
    ]
    for row in rows:
        cells = [str(c).replace("|", "\\|") for c in list(row)[:ncols]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_health_status(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    rows = [
        ("Host",       f"`{d.get('host', '—')}`"),
        ("Server ID",  f"`{d.get('serverId', '—')}`"),
        ("Timestamp",  _fmt_ts(d.get("timestamp"))),
        ("Apps",       f"`{d.get('apps', '—')}`"),
        ("Adapters",   f"`{d.get('adapters', '—')}`"),
    ]
    for svc in d.get("services", []):
        label = svc.get("service", "service").capitalize()
        rows.append((label, f"`{svc.get('status', '—')}`"))
    return _table(["Field", "Value"], rows) + "\n\n"


def _render_server(d):
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
    if deps:
        rows.append(("Core deps", ", ".join(f"`{k}@{v}`" for k, v in deps.items())))
    return _table(["Field", "Value"], rows) + "\n\n"


def _render_adapters(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    results = d.get("results", [])
    if not results:
        return "_No adapters found._\n\n"
    rows = []
    for a in sorted(results, key=lambda x: (x.get("state", ""), x.get("id", ""))):
        conn  = (a.get("connection") or {}).get("state", "—")
        rss   = _fmt_bytes((a.get("memoryUsage") or {}).get("rss", 0))
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
    return _table(["ID", "Package", "Version", "State", "Connection", "Uptime", "RSS"], rows) + f"\n\n**Total: {total}**\n\n"


def _render_applications(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    results = d.get("results", [])
    if not results:
        return "_No applications found._\n\n"
    rows = []
    for a in sorted(results, key=lambda x: x.get("id", "")):
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
    return _table(["ID", "Package", "Version", "State", "Uptime", "RSS"], rows) + f"\n\n**Total: {total}**\n\n"


def _render_integrations(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    if isinstance(d, list):
        results, total = d, len(d)
    else:
        # Response uses "integrationModels" key
        results = d.get("integrationModels", d.get("results", d.get("items", [])))
        total   = d.get("total", len(results))
    if not results:
        return "_No integration models found._\n\n"
    rows = []
    for m in results:
        name = m.get("versionId", m.get("model", "—"))
        desc = (m.get("description") or "").replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:87] + "..."
        props = m.get("properties", {})
        server = props.get("server", {})
        host = f"{server.get('protocol', '')}://{server.get('host', '')}".strip("://") if server else "—"
        rows.append([f"`{name}`", host, desc])
    return _table(["Model", "Host", "Description"], rows) + f"\n\n**Total: {total}**\n\n"


def _render_config(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    # /server/config returns a flat array of {name, origin, value} entries
    if isinstance(d, list) and d and isinstance(d[0], dict) and "name" in d[0] and "origin" in d[0]:
        rows = []
        for entry in d:
            name  = entry.get("name", "")
            origin = entry.get("origin", "")
            value  = entry.get("value", "")
            # Redact if IAP already masked it or any underscore-delimited token in the
            # name exactly matches a sensitive key (avoids "bypass" matching "pass", etc.)
            name_tokens = set(name.lower().split("_"))
            if value == "********" or name_tokens & _SENSITIVE_KEYS:
                value = "`[REDACTED]`"
            else:
                value = f"`{value}`" if value != "" else "—"
            rows.append([f"`{name}`", origin, value])
        return _table(["Name", "Origin", "Value"], rows) + "\n\n"
    # Fallback for unexpected shapes
    return "```json\n" + json.dumps(_redact(d), indent=2) + "\n```\n\n"


def _render_workers(d):
    if "_error" in d:
        return f"> **Error:** {d['_error']}\n\n"
    rows = []
    for label, key in [("Job Worker", "jobWorker"), ("Task Worker", "taskWorker")]:
        w = d.get(key, {})
        rows.append([
            label,
            "Yes" if w.get("running") else "No",
            w.get("clusterValue", "—"),
            w.get("localValue", "—"),
            str(w.get("startupValue", "—")),
        ])
    return _table(["Worker", "Running", "Cluster Value (Central)", "Local Value", "Startup Value"], rows) + "\n\n"


# ── Kubernetes ───────────────────────────────────────────────────────────────

_K8S_CHECKS = [
    ("Pods",                     ["get", "pods", "-o", "wide"],                        True),
    ("StatefulSets",             ["get", "statefulsets"],                              True),
    ("Services",                 ["get", "services"],                                  True),
    ("Ingress",                  ["get", "ingress"],                                   True),
    ("Persistent Volume Claims", ["get", "pvc"],                                       True),
    ("ConfigMaps",               ["get", "configmaps"],                                True),
    ("Nodes",                    ["get", "nodes"],                                     False),
    ("Pod Resource Usage",       ["top", "pods"],                                      True),
    ("Events",                   ["get", "events", "--sort-by=.lastTimestamp"],        True),
]


def _kubectl_available():
    return shutil.which("kubectl") is not None


def _kubectl_run(args, namespace=None):
    """Run a kubectl command. Returns output string on success, None on any failure."""
    cmd = ["kubectl"] + args + (["-n", namespace] if namespace else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _kubectl_context_namespace():
    """Return the namespace from the active kubectl context, or 'default'."""
    r = subprocess.run(
        ["kubectl", "config", "view", "--minify", "-o",
         "jsonpath={.contexts[0].context.namespace}"],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip() or "default"


def _collect_k8s(namespace):
    data = {"namespace": namespace}
    for label, args, namespaced in _K8S_CHECKS:
        data[label] = _kubectl_run(args, namespace=namespace if namespaced else None)
    return data


def _render_k8s(data):
    out = [f"**Namespace:** `{data['namespace']}`\n\n"]
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


def _collect(base, username, password, ctx, token=None):
    if not token:
        token = _login(base, username, password, ctx)
    if not token:
        return {"ok": False}
    # Validate the token with a lightweight authenticated endpoint before proceeding
    probe = _get(base, "/health/server", token, ctx)
    if "_error" in probe and "401" in str(probe.get("_error", "")):
        return {"ok": False, "_reason": "token_expired"}
    result = {"ok": True}
    for key, path in _ENDPOINTS:
        result[key] = _get(base, path, token, ctx)
    return result


# ── Report assembly ───────────────────────────────────────────────────────────

def _build_report(hosts, results, generated_at, k8s_data=None):
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
        out.append(_render_k8s(k8s_data))
        out.append("---\n\n")

    for host, r in zip(hosts, results):
        out.append(f"## `{host}`\n\n")

        if not r["ok"]:
            if r.get("_reason") == "token_expired":
                out.append("**Login:** token expired or invalid — obtain a fresh session token and re-run.\n\n---\n\n")
            else:
                out.append("**Login:** failed\n\n---\n\n")
            continue

        out.append("**Login:** ok\n\n")

        out.append("### Health Status\n\n")
        out.append(_render_health_status(r["health_status"]))

        out.append("### Server Info\n\n")
        out.append(_render_server(r["health_server"]))

        out.append("### Adapters\n\n")
        out.append(_render_adapters(r["health_adapters"]))

        out.append("### Applications\n\n")
        out.append(_render_applications(r["health_apps"]))

        out.append("### Integration Models\n\n")
        out.append(_render_integrations(r["integrations"]))

        out.append("### Worker Status\n\n")
        out.append(_render_workers(r["worker_status"]))

        out.append("### Server Configuration\n\n")
        out.append(_render_config(r["server_config"]))

        out.append("---\n\n")

    return "".join(out)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = _parse_args()

    # Collect hosts — prompt interactively if none provided
    hosts = list(dict.fromkeys(args.hosts or []))
    if not hosts:
        url = input("IAP host URL (e.g. https://iap.example.com): ").strip()
        if not url:
            sys.exit("Error: no host provided.")
        hosts = [url]

    ctx = _ssl_ctx(args.ca_cert)

    # Auth: pre-supplied token (SSO) takes priority over username/password
    supplied_token = args.token or None
    if supplied_token:
        password = None
        prompted = True
        print("Using supplied session token (SSO mode).")
    else:
        using_default_creds = (args.username == _DEFAULT_USER)
        if using_default_creds:
            password = _DEFAULT_PASS
            prompted = False
        else:
            password = getpass.getpass(f"Password for '{args.username}': ")
            prompted = True

    print(f"\nCertifying {len(hosts)} node(s)...\n")

    all_results = []
    for host in hosts:
        print(f"  {host}")
        result = _collect(host, args.username, password, ctx, token=supplied_token)

        # If default admin/admin was rejected, prompt once and reuse for remaining hosts
        if not result["ok"] and not prompted:
            print(f"    Default credentials rejected.")
            password = getpass.getpass(f"    Password for '{args.username}': ")
            prompted = True
            result = _collect(host, args.username, password, ctx)

        if not result["ok"]:
            if result.get("_reason") == "token_expired":
                print(f"    Login: token expired or invalid — grab a fresh token and re-run.")
            else:
                print(f"    Login: failed")
        else:
            print(f"    Login: ok")
            for key, path in _ENDPOINTS:
                status = "ok" if "_error" not in result[key] else f"error ({result[key].get('_error', '')})"
                print(f"    {path}: {status}")

        all_results.append(result)

    # Kubernetes resource collection
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

    now = datetime.now(tz=timezone.utc)
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    report = _build_report(hosts, all_results, generated_at, k8s_data=k8s_data)

    # Filename uses the first host's hostname and today's date
    hostname = _hostname(hosts[0])
    outfile  = f"iap-certify-{hostname}-{now.strftime('%Y-%m-%d')}.md"

    with open(outfile, "w", encoding="utf-8") as fh:
        fh.write(report)

    print(f"\nReport written to: {outfile}")


if __name__ == "__main__":
    main()
