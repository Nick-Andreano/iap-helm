# IAP Helm Chart - Claude Context

## Overview

This is the Helm chart for the **Itential Automation Platform (IAP)** — a network automation platform deployed as a Kubernetes StatefulSet. The chart lives in `charts/iap/` and is versioned separately from the repo.

- **Chart version**: 1.12.0
- **App version**: 6.0.7 (IAP release)
- **Helm requirement**: v3.15.0+
- **Chart type**: application

---

## Repository Layout

```
iap-helm/
├── charts/iap/               # The Helm chart
│   ├── Chart.yaml
│   ├── Chart.lock
│   ├── values.yaml           # Canonical defaults — the only values file to reference
│   ├── templates/
│   │   ├── _helpers.tpl
│   │   ├── NOTES.txt
│   │   ├── statefulset.yaml
│   │   ├── serviceaccount.yaml
│   │   ├── service.yaml
│   │   ├── service-headless.yaml
│   │   ├── configmap.yaml
│   │   ├── configmap-adapter-installer.yaml
│   │   ├── ingress.yaml
│   │   ├── issuer.yaml
│   │   ├── certificate.yaml
│   │   ├── serviceaccount.yaml
│   │   ├── storage-class.yaml
│   │   ├── PodMonitor.yaml
│   │   ├── deployment-job-metrics-exporter.yaml
│   │   ├── service-job-metrics-exporter.yaml
│   │   ├── podmonitor-job-metrics-exporter.yaml
│   │   └── tests/
│   │       ├── test-post-install.yaml
│   │       └── test-rbac.yaml
│   ├── crds/
│   │   └── monitoring.coreos.com_podmonitors.yaml
│   └── charts/               # Vendored dependencies
│       ├── cert-manager-v1.12.3.tgz
│       └── external-dns-1.17.0.tgz
├── docs/
│   ├── helm-tests.md
│   ├── ingress.md
│   ├── adapter-installer.md
│   └── pre-install-verification.md
├── scripts/
│   ├── verify.py             # pre-install / pre-upgrade environment verification
│   └── certify.py            # post-install certification report
└── README.md
```

---

## Chart Dependencies

| Dependency | Version | Condition |
|---|---|---|
| cert-manager | 1.12.3 | `certManager.enabled` |
| external-dns | 1.17.0 | `external-dns.enabled` |

Both are vendored in `charts/iap/charts/` and enabled via feature flags in `values.yaml`.

---

## Core Workload

IAP runs as a **StatefulSet** (`templates/statefulset.yaml`) with:

- `replicaCount: 2` by default
- Each pod gets a stable DNS name via a headless service
- Persistent volume claim templates for `/opt/itential/iap/current/platform/adapters` (assets, 20Gi) and `/var/log/itential` (logs, 10Gi)

### Ports

| Name | Container Port | Purpose |
|---|---|---|
| `http` | 3443 | Main IAP application (HTTP/HTTPS) |
| `websocket` | 8080 | WebSocket traffic |
| `metrics` | 9256 | Process exporter (if sidecar enabled) |

### Probes

- **Startup**: HTTP GET `/health/status` — 180s initial delay, 30s period
- **Liveness**: `exec` checking for core Pronghorn processes — 90s initial delay, 30s period
- **Readiness**: HTTP GET `/health/status?exclude-services=true` — 90s initial delay, 30s period

### Security Context

Runs as non-root: `runAsUser: 1001`, `fsGroup: 1001`, `runAsNonRoot: true`, `allowPrivilegeEscalation: false`.

### Resources

Default requests: `cpu: 3`, `memory: 14Gi`. Default limit: `memory: 14Gi`.

---

## Template Helper Functions (`_helpers.tpl`)

| Helper | Purpose |
|---|---|
| `iap.name` | Chart name, max 63 chars |
| `iap.fullname` | Fully qualified app name (release + chart) |
| `iap.chart` | Chart label with version |
| `iap.labels` | Standard `app.kubernetes.io/*` + `helm.sh/*` labels |
| `iap.selectorLabels` | Labels used for pod selection |
| `iap.annotations` | Common annotations (copyright, license, template file) |
| `iap.serviceAccountName` | Effective SA name — falls back to fullname when `create: true` and `name` is empty |
| `iap.ingressTLSHosts` | Full TLS hostname list (load balancer + per-pod direct access) for ingress |
| `iap.DirectAccessHost` | Generates per-pod hostname for direct ingress access |
| `iap.jobMetricsExporterHost` | Generates the job-metrics-exporter ingress hostname: `{fullname}-{namespace}-job-metrics.{baseDomain}` |

---

## Services

### `service.yaml` — Load-Balanced ClusterIP

Single entrypoint for all replicas. Ports:
- 443 → container 3443 (HTTPS)
- 8080 → container 8080 (WebSocket, if `useWebSockets: true`)

### `service-headless.yaml` — Per-Pod Services

Generates one headless service per replica (`{service}-headless-0`, `{service}-headless-1`, ...). Used by:
- Direct ingress access
- Post-install test job (tests each pod individually)
- Process exporter metrics scraping

---

## Ingress (`ingress.yaml`)

Two access patterns, both configurable independently:

### Load Balancer Access
- Single hostname (e.g., `iap.example.com`) routing to all pods
- WebSocket path (`/ws`) support
- Controlled by `ingress.loadBalancer.enabled`

### Direct Access
- One hostname per pod: `{fullname}-{namespace}-{index}.{baseDomain}`
- Optional `hostOverride` prefix for custom hostnames
- Includes `/metrics` path for process exporter (if enabled)
- Controlled by `ingress.directAccess.enabled`

### Job Metrics Exporter Access
- Single hostname: `{fullname}-{namespace}-job-metrics.{baseDomain}`
- Routes `/metrics` to the `job-metrics-exporter` service
- Rendered when `jobMetricsExporter.enabled`, `jobMetricsExporter.ingressEnabled`, and `ingress.directAccess.enabled` are all true

Both support configurable `ingressClassName`, TLS sections, and annotations (for ALB or NGINX).

---

## TLS / Certificate Management

### cert-manager (`issuer.yaml`, `certificate.yaml`)

When `certManager.enabled: true` and `issuer.enabled: true`:
- Creates a CA-based `Issuer` or `ClusterIssuer` from a named Kubernetes secret
- Issues a `Certificate` covering the load balancer hostname and all per-pod DNS names
- Duration: 2160h (90 days), renews 48h before expiry
- Certificate stored in a Kubernetes secret, mounted into each pod

### Additional TLS Secrets

`additionalTLSSecrets` in values allows mounting extra certificates (client certs, CA bundles) into pods at custom paths.

### Required Secrets

These Kubernetes secrets must exist before install:

| Secret | Contents |
|---|---|
| `itential-platform-secrets` | All IAP environment secrets (DB passwords, API keys, etc.) |
| `itential-job-metrics-secrets` | MongoDB URI for the Job Metrics Exporter (read-only user). Required when `jobMetricsExporter.enabled: true`. |
| `iap-ca` | CA certificate for cert-manager issuer |
| Image pull secret(s) | Named in `imagePullSecrets` |

---

## Adapter Installer Init Container

Controlled by `initAdapterInstaller.enabled: true` + `mountAdapterVolume: true`.

- Runs a Node.js 18 init container before IAP starts
- Executes a bash script (`configmap-adapter-installer.yaml`) that:
  - Clones git repos (GitLab, GitHub, Bitbucket) into the adapters volume
  - Detects branch/tag changes and reinstalls only when needed
  - Keeps an installed adapter (with its `node_modules`) when the git remote cannot be reached
  - Removes adapters no longer in the list
  - Runs `npm install` with configurable options
  - Sets permissions (775, itential:users)
- Pod restarts automatically when the ConfigMap checksum changes (annotation-based)

Repository list format supports both strings (`"https://repo-url"`) and objects with `url`, `branch`/`tag` fields. An object with `type: folders` (optional `path`) installs every folder of one repository that contains a `package.json`: each with `npm install`, except folders matching the optional `skipNpmInstall` globs (installed as committed); folders removed from git are removed from the volume. `initAdapterInstaller.gitCredentials.existingSecret` supplies HTTPS credentials for private repositories. `initAdapterInstaller.env` (a name-to-value map) sets extra environment variables on the init container, for example an HTTP proxy.

---

## Post-Install Tests (`tests/`)

Controlled by `postInstallTests.enabled: true`.

### `test-post-install.yaml`
- Runs as a Kubernetes Job after `helm install`/`helm upgrade`
- Creates its own ServiceAccount + RBAC (ClusterRole + ClusterRoleBinding)
- Tests each pod replica via headless service DNS
- Validates:
  1. `/health/status` — apps, adapters, Redis, MongoDB all healthy
  2. `/version` — matches deployed image tag
  3. Process count — 14 required core Pronghorn processes + configurable optional list
- Retries every 15s up to `readinessTimeout` (default 300s)
- Auto-cleans up after `ttlSecondsAfterFinished` (default 300s)

Run tests: `helm test <release-name> -n <namespace> --logs`

---

## Process Exporter Sidecar

Controlled by `processExporter.enabled: true`.

- Sidecar container scraping process metrics via `/metrics` on port 9256
- `configmap.yaml` provides `process-exporter.conf` and `web-config.yaml` (TLS for metrics)
- `PodMonitor.yaml` creates a Prometheus Operator `PodMonitor` CRD resource for scraping
- Metrics accessible via direct-access ingress `/metrics` path

---

## Job Metrics Exporter (`deployment-job-metrics-exporter.yaml`)

Controlled by `jobMetricsExporter.enabled: true`.

- Standalone Deployment with `replicas: 1` — a global MongoDB observer, not per-pod. A sidecar would produce duplicate metric series with multiple IAP replicas.
- Image: `ghcr.io/itential/job-metrics-exporter` (public GHCR, no pull secret needed)
- Listens on port 9477, exposes `/metrics` and `/healthz`
- Requires a dedicated `itential-job-metrics-secrets` Kubernetes secret with a **read-only** MongoDB user (separate from the IAP `itential` user which has read/write access)
- `mongoDatabase` must be set to match `ITENTIAL_MONGO_DB_NAME` (e.g. `"itential-na"`); leaving it empty uses the exporter's built-in default database name
- When `useTLS: true`: mounts the cert from `certificate.secretName` at `/etc/ssl/platform` and sets TLS env vars; probes use `HTTPS` scheme
- `service-job-metrics-exporter.yaml` — ClusterIP service on port 9477
- `podmonitor-job-metrics-exporter.yaml` — PodMonitor for Prometheus scraping (15s interval); sets `scheme: https` + `tlsConfig.insecureSkipVerify: true` when `useTLS: true`
- Ingress rule added to `ingress.yaml` at hostname `{fullname}-{namespace}-job-metrics.{baseDomain}/metrics` when `jobMetricsExporter.ingressEnabled: true` and `ingress.directAccess.enabled: true`

---

## Storage (`storage-class.yaml`)

Creates a `StorageClass` when `storageClass.enabled: true`:
- Provisioner and parameters are passed through directly (e.g., AWS EBS GP3, NFS)
- Reclaim policy: `Retain`
- Volume binding: `WaitForFirstConsumer`
- Used by StatefulSet PVC templates for assets and logs volumes

---

## Key values.yaml Sections

| Section | Key Fields | Notes |
|---|---|---|
| Root | `replicaCount`, `applicationPort`, `websocketPort`, `useTLS`, `useWebSockets` | Core deployment config |
| `image` | `repository`, `tag`, `pullPolicy` | IAP container image |
| `imagePullSecrets` | list of secret names | Must exist in namespace |
| `initAdapterInstaller` | `enabled`, `repositories`, `npmOptions` | See adapter-installer.md |
| `postInstallTests` | `enabled`, `readinessTimeout`, `ttlSecondsAfterFinished`, `optionalProcesses` | See helm-tests.md |
| `certManager` | `enabled` | Toggles cert-manager dependency |
| `issuer` | `enabled`, `name`, `caSecretName`, `kind` | Issuer or ClusterIssuer |
| `certificate` | `enabled`, `secretName`, `commonName`, `dnsNames`, `ipAddresses` | SAN/IP config |
| `ingress` | `loadBalancer.*`, `directAccess.*` | See ingress.md |
| `storageClass` | `enabled`, `name`, `provisioner`, `parameters` | Storage provisioner |
| `resources` | `requests`, `limits` | CPU/memory |
| `env` | 350+ env vars | MongoDB, Redis, Vault, auth, logging, SNMP |
| `processExporter` | `enabled`, `image`, `config` | Prometheus sidecar |
| `jobMetricsExporter` | `enabled`, `ingressEnabled`, `image`, `port`, `mongoSecretName`, `mongoSecretKey`, `mongoDatabase`, `changeStreamEnabled`, `pollingEnabled`, `logLevel`, `logFormat` | Standalone Job Metrics Exporter Deployment |
| `serviceAccount` | `create`, `name`, `annotations`, `automountServiceAccountToken` | SA creation + cloud IAM federation (IRSA/Workload Identity) |
| `hostAliases` | list | For Redis Sentinel DNS resolution |
| `nodeSelector`, `tolerations`, `affinity` | standard k8s scheduling | |
| `additionalTLSSecrets` | list of `{secretName, mountPath}` | Extra cert mounts |

---

## Environment Variables (`.env` section in values.yaml)

IAP is configured almost entirely through environment variables passed to the container. The `env` block in `values.yaml` covers:

- **MongoDB**: `IAPP_DB_URL`, `IAPP_DB_AUTHENTICATION_ENABLED`, `IAPP_DB_SSL_ENABLED`
- **Redis**: `IAPP_REDIS_PORT`, `IAPP_REDIS_AUTH_ENABLED`, sentinel config
- **HashiCorp Vault**: `IAPP_VAULT_ENABLED`, read-only integration
- **Auth**: `IAPP_ENABLE_DEFAULT_USER`, `IAPP_DEFAULT_USER_SESSION_TTL`
- **Webserver**: `IAPP_HTTPS_ENABLED`, `IAPP_HTTPS_PORT`, CORS, timeouts
- **Logging**: file rotation, syslog, console log levels
- **SNMP**: trap configuration
- **Workers**: task and job worker settings

Sensitive values (passwords, tokens) come from the `itential-platform-secrets` Kubernetes secret via `envFrom`.

---

## NOTES.txt Output

After install, Helm prints:
- Application URL (HTTP or HTTPS depending on `useTLS`)
- Per-pod direct access URLs
- Default admin credentials
- Summary of configured environment variables
- Links to Itential documentation

---

## Unit Tests (`charts/iap/tests/`)

The chart uses [helm-unittest](https://github.com/helm-unittest/helm-unittest). Test files live in `charts/iap/tests/*_test.yaml`.

- Run: `helm unittest charts/iap`
- Test release name resolves to `RELEASE-NAME`, namespace to `NAMESPACE`
- `iap.fullname` renders as `RELEASE-NAME-iap` in test assertions
- Each template has a corresponding `<template-name>_test.yaml`; add tests whenever a template changes

---

## Template Conventions

Every resource template must follow this pattern for labels and annotations.

**Labels** — always include both:
```yaml
labels:
  {{- include "iap.labels" . | nindent 4 }}
  app.kubernetes.io/component: "<resource-type>"
```

**Annotations** — always unconditional (never wrap the whole block in `{{- with }}`):
```yaml
annotations:
  kubernetes.io/description: "Itential Automation Platform <resource-type>."
  {{- include "iap.annotations" . | nindent 4 }}
  {{- with .Values.<section>.annotations }}
  {{- toYaml . | nindent 4 }}
  {{- end }}
```

When the same computed value is needed in more than one template, extract it to `_helpers.tpl`.

---

## Scripts (`scripts/`)

- `verify.py install|upgrade -n <ns> -f <values>`: runs before an install or upgrade. Read-only `kubectl` checks of the secrets, StorageClass and PVCs, and cert-manager objects. Exit code 1 on any failure. Documented in `docs/pre-install-verification.md`.
- `certify.py --host <url> [--host <url> ...] -n <ns>`: runs after an install. Calls the platform APIs on each node (and reads Kubernetes resources when `kubectl` is available) and writes `iap-certify-<hostname>-<date>.md`. TLS is only verified when `--ca-cert` is given.

## Docs

- [README.md](README.md) — Installation, dependencies, secrets setup, values reference
- [docs/ingress.md](docs/ingress.md) — ALB vs NGINX, direct access, TLS, annotations
- [docs/helm-tests.md](docs/helm-tests.md) — Test execution, CI/CD integration, troubleshooting
- [docs/adapter-installer.md](docs/adapter-installer.md) — Adapter lifecycle, repo config, debugging
