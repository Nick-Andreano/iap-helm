# InitContainer Adapter Installer

The InitContainer Adapter Installer is a powerful feature that automatically installs and manages Platform adapters from git repositories (public, or private with credentials) into the Itential Automation Platform persistent volume. This documentation covers configuration, usage, and troubleshooting.

## Overview

The adapter installer uses a Kubernetes initContainer that runs before the main IAP container starts. It clones Node.js adapters from specified repositories, installs their dependencies, and manages their lifecycle through upgrades and removals.

## Key Features

- **Smart Version Detection**: Only reinstalls adapters when versions change
- **Node.js Version Awareness**: Reinstalls an adapter when the Node.js major version of the init container differs from the one it was installed with (for example Node.js 20 to 22)
- **Automatic Cleanup**: Removes obsolete adapters not in the repositories list
- **Keeps Installed Adapters When Git Is Unreachable**: An expired token, a network problem or a git host outage does not remove an adapter that is already installed
- **Branch/Tag Support**: Install specific versions or use default branches
- **Multi-Repository**: Support for multiple adapter repositories
- **One Repository, Many Adapters**: `type: folders` installs every adapter folder of a single repository and rebuilds only the folders that changed
- **Private Git Repositories**: HTTPS credentials from a Kubernetes Secret (`gitCredentials`)
- **Comprehensive Reporting**: Detailed installation and error reporting
- **Security**: Runs as non-root user with restricted permissions
- **Automatic Restarts**: Pods restart automatically when repository configuration changes

## Prerequisites

- `mountAdapterVolume` must be set to `true` (the installer needs a persistent volume to install adapters)
- Persistent volume claims must be enabled
- Network access to Git repositories (GitLab, GitHub, Bitbucket, etc.)

## Configuration

### Basic Configuration

Enable the adapter installer in your `values.yaml`:

```yaml
# Enable adapter volume mounting
mountAdapterVolume: true

# Configure the adapter installer
initAdapterInstaller:
  enabled: true
  image:
    repository: node
    tag: ""                  # Empty: 22-bullseye for Platform 6.6.0 and later, 18-bullseye below. Set to pin a version.
    pullPolicy: IfNotPresent
  securityContext:
    runAsUser: 1001
    runAsGroup: 1001
    runAsNonRoot: true
    allowPrivilegeEscalation: false
  repositories:
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-example.git"
      branch: "v1.2.3"
    - "https://gitlab.com/itentialopensource/adapters/adapter-other.git"
```

### Repository Configuration

Repositories can be configured in three formats:

#### 1. String Format (Default Branch)
```yaml
repositories:
  - "https://gitlab.com/itentialopensource/adapters/adapter-example.git"
```

#### 2. Object Format (Specific Branch/Tag)
```yaml
repositories:
  - url: "https://gitlab.com/itentialopensource/adapters/adapter-example.git"
    branch: "v1.2.3"  # Can be a branch name or tag
```

#### 3. Multiple Git Providers
```yaml
repositories:
  # GitLab
  - url: "https://gitlab.com/itentialopensource/adapters/adapter-six_connect.git"
    branch: "v0.8.1"
  # GitHub  
  - url: "https://github.com/company/adapter-custom.git"
    branch: "v1.0.0"
  # Bitbucket
  - url: "https://bitbucket.org/team/adapter-internal.git"
    branch: "main"
```

#### 4. One Repository With Many Adapter Folders (`type: folders`)
```yaml
repositories:
  - url: "https://git.example.com/team/iap-adapters.git"
    type: folders
    branch: develop      # optional
    path: ""             # optional: directory inside the repository that holds the folders
    skipNpmInstall: []   # optional: glob patterns of folders that are installed as committed, e.g. ["app-*"]
```
Every folder (at the repository root, or under `path`) that contains a `package.json` is installed as its own
directory in `/opt/itential/platform/services/custom/<folder>`.

- Every folder gets `npm install` (with `npmInstallOptions`), except folders matching a glob in `skipNpmInstall` (for example `"app-*"`). Those are installed as committed: their `node_modules` come from git and `npm install` is not run. Note that `npm install` removes packages that are not in a folder's `package.json`, so use `skipNpmInstall` for folders that ship a curated `node_modules`.
- On a restart, one `git ls-remote` call decides whether anything changed. If not, nothing is fetched.
- If the commit changed, the commit is fetched once (shallow, no checkout) and each folder is compared by its git tree hash. Only changed folders are rebuilt.
- A folder is built in a temporary directory and swapped into place. If `npm install` fails, the previously installed copy is kept and the failure is reported.
- A folder that is removed from git is removed from the volume. Only folders installed from that repository entry are removed, and nothing is removed if no folder is found or git cannot be reached.
- Folders without `package.json` and hidden folders are ignored. Folder names must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- A folder name cannot also be used by a single-adapter entry, and a folder installed by one `folders` entry is not taken over by another.
- The installer keeps its markers and temporary directories in `/opt/itential/platform/services/custom/.adapter-installer`.
- Size the volume for the checked-out folders plus a temporary copy of the repository during an update.

##### Example repository layout

A repository with the adapter folders at its root (`path` left empty), and `skipNpmInstall: ["app-*"]`:

```
iap-adapters/                      <- repository root, branch "develop"
├── .gitignore                     ignored: files are never installed
├── README.md                      ignored: files are never installed
├── .github/                       ignored: hidden folder
├── docs/                          ignored: no package.json
│   └── notes.md
├── adapter-email/                 installed, npm install runs
│   ├── package.json               required, a folder without it is skipped
│   ├── adapter.js
│   └── pronghorn.json
├── adapter-servicenow/            installed, npm install runs
│   ├── package.json
│   └── adapter.js
└── app-artifacts/                 matches "app-*": installed as committed, no npm install
    ├── package.json
    └── node_modules/              committed to git and used as is
        └── ...
```

Each folder with a `package.json` becomes its own directory on the volume. The installer adds its own working folder next to them:

```
/opt/itential/platform/services/custom/
├── adapter-email/
│   ├── package.json
│   └── node_modules/              created by npm install
├── adapter-servicenow/
│   ├── package.json
│   └── node_modules/              created by npm install
├── app-artifacts/
│   ├── package.json
│   └── node_modules/              the one committed to git
└── .adapter-installer/            markers and temporary folders, managed by the installer
```

If the folders sit in a subdirectory of the repository, set `path` to it. Only the folders under `path` are installed, and everything else in the repository is ignored:

```
iap-adapters/                      <- repository root
├── README.md                      ignored
├── tools/                         ignored: outside path
└── adapters/                      <- path: "adapters"
    ├── adapter-email/             installed
    │   └── package.json
    └── adapter-servicenow/        installed
        └── package.json
```

```yaml
repositories:
  - url: "https://git.example.com/team/iap-adapters.git"
    type: folders
    branch: develop
    path: adapters
```

### Private Git Repositories

Set `initAdapterInstaller.gitCredentials.existingSecret` to a Secret with `username` and `password` keys (HTTPS). It is mounted read-only into the init container and handed to git through `GIT_ASKPASS`. The token is not written to the volume, the pod spec or the logs.

```bash
kubectl create secret generic adapter-git-credentials --from-literal=username=<user> --from-literal=password=<token>
```

### Advanced Configuration

```yaml
initAdapterInstaller:
  enabled: true
  image:
    repository: node
    tag: ""                  # Empty: 22-bullseye for Platform 6.6.0 and later, 18-bullseye below. Set to pin a version.
    pullPolicy: IfNotPresent
  securityContext:
    runAsUser: 1001          # Must match podSecurityContext
    runAsGroup: 1001         # Must match podSecurityContext
    runAsNonRoot: true
    allowPrivilegeEscalation: false
  repositories:
    # Mix of formats is supported
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-six_connect.git"
      branch: "v0.8.1"
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-aruba_central.git" 
      branch: "v2.3.0"
    - "https://gitlab.com/itentialopensource/adapters/adapter-github.git"
  # Custom npm install options (optional)
  npmInstallOptions:
    - "--silent"
    - "--no-audit" 
    - "--no-fund"
    - "--production"  # Example additional option
```

### NPM Install Options

The adapter installer allows customization of npm install command options through the `npmInstallOptions` configuration:

```yaml
initAdapterInstaller:
  # Default options (used if npmInstallOptions is not specified)
  npmInstallOptions:
    - "--silent"      # Suppress npm output
    - "--no-audit"    # Skip security audit
    - "--no-fund"     # Skip funding messages
```

**Common Options:**
- `--production`: Install only production dependencies
- `--ignore-scripts`: Skip running package scripts
- `--no-optional`: Skip optional dependencies  
- `--legacy-peer-deps`: Use legacy peer dependency resolution
- `--force`: Force npm to ignore conflicts

**Example Custom Configuration:**
```yaml
npmInstallOptions:
  - "--silent"
  - "--no-audit"
  - "--production"
  - "--ignore-scripts"
```

### Node.js Version and Native Add-ons

Compiled dependencies are tied to the Node.js version they were built with. Itential Platform 6.6.0 and later run on Node.js 22 (earlier releases ran on Node.js 20), and every sideloaded adapter runs on the Platform's Node.js, so adapters must be built with the same major version.

- **The init container image sets the Node.js version.** When `initAdapterInstaller.image.tag` is empty (the default), the chart selects it from the Platform image tag (`image.tag`): `22-bullseye` for Platform 6.6.0 and later, and for tags that are not a version (such as `latest`); `18-bullseye`, the previous default, for earlier releases. Set `initAdapterInstaller.image.tag` to pin a version, and keep it on the same Node.js major version as the Platform image. For a Platform release that runs Node.js 20, set `tag: "20-bullseye"`, which is the exact match.
- **The installer records the major version.** After a successful `npm install` it writes the Node.js major version (for example `22`) to `.installer-node-major` in the adapter directory.
- **A different major version triggers a reinstall.** On every start the installer compares that file to the Node.js version of the init container. If they differ, the adapter is removed, cloned again and reinstalled, even when the git commit has not changed. The log shows `adapter-x was installed with Node.js 20, this init container runs Node.js 22, will reinstall`. Patch and minor changes (22.11 to 22.12) do not trigger a reinstall.
- **Adapters installed by an older chart version have no marker** and are reinstalled once on the first start after the upgrade. See [Upgrading Helm Chart](#upgrading-helm-chart).
- **If the git remote is unreachable**, an adapter with a different Node.js major version is kept (it cannot be reinstalled) and the log shows `WARNING: <adapter> was not installed with Node.js 22 and the git remote is unreachable, keeping the installed copy`. Fix the connection and restart the pod to rebuild it.
- **`type: folders` entries follow the same rule**, except for folders that match `skipNpmInstall`. Those ship their `node_modules` in git, so the installer does not rebuild them. Their `node_modules` must be built with the Node.js version of the Platform before they are committed.

**Adapters with native add-ons.** The default `npmInstallOptions` include `--ignore-scripts`, which stops dependency install scripts from running. Dependencies that compile a native add-on (`node-gyp`) are then not built. For an adapter that has them, remove `--ignore-scripts` so the add-on is compiled with the init container's Node.js:

```yaml
initAdapterInstaller:
  npmInstallOptions:
    - "--silent"
    - "--no-audit"
    - "--no-fund"
```

`npmInstallOptions` applies to every adapter in the list, and without `--ignore-scripts` the install scripts of all dependencies of all adapters run. Only do this for repositories you trust. The `node` image includes the build tools (`python3`, `make`, `g++`) that `node-gyp` needs.

**Other Node.js 22 checks.** A reinstall does not fix adapter code that uses APIs removed in Node.js 22, such as `crypto.createCipher` and `crypto.createDecipher` (use `createCipheriv` and `createDecipheriv`), or `server.maxConnections = 0`, which Node.js 22 treats literally and rejects every connection. See the [Node.js 22 breaking changes](https://docs.itential.com/itential-platform/release-notes/breaking-changes/node-22-runtime-upgrade) and test the adapters on Node.js 22 in a development environment first.

### Environment Variables (for example an HTTP proxy)

`initAdapterInstaller.env` is a map of environment variable names to values that is set on the installer init container. Use it when git or npm must go through an HTTP proxy:

```yaml
initAdapterInstaller:
  enabled: true
  env:
    HTTP_PROXY: "http://proxy.example.com:3128"
    http_proxy: "http://proxy.example.com:3128"
    HTTPS_PROXY: "http://proxy.example.com:3128"
    https_proxy: "http://proxy.example.com:3128"
    NO_PROXY: ".svc,.cluster.local,10.0.0.0/8,git.internal.example.com"
    no_proxy: ".svc,.cluster.local,10.0.0.0/8,git.internal.example.com"
```

- **Set both the upper and lower case names.** Git reads proxy settings through libcurl, which ignores an upper case `HTTP_PROXY` and only honors a lower case `http_proxy` for plain `http://` URLs. `HTTPS_PROXY` and `NO_PROXY` work in either case.
- **List internal hosts in `NO_PROXY`.** Include `.svc`, `.cluster.local`, your cluster's address ranges and any internal git server, or those requests go to the proxy.
- **npm uses the same variables**, so `npm install` also reaches the registry through the proxy.
- The variables are set on the init container only. To set variables for the Itential Platform container use the top level `env`.
- Do not set `GIT_ASKPASS` or `GIT_TERMINAL_PROMPT` here. The chart sets them when `gitCredentials` is used.
- Changing `env` changes the pod template, so the pods restart.
- A proxy that inspects TLS needs its CA certificate trusted by git and npm (`GIT_SSL_CAINFO`, `NODE_EXTRA_CA_CERTS`). Mounting a CA bundle into the init container is not supported yet.

## How It Works

### Installation Process

1. **Pod-0 Only Execution**: Only runs on the first replica to avoid redundant installations
2. **Cleanup Phase**: Removes adapters not in the current repositories list
3. **Version Detection**: Checks if existing adapters need updates using git commit comparison, and whether they were installed with the Node.js major version of the init container
4. **Installation Phase**: Clones and installs new/updated adapters, and records the Node.js major version in each adapter
5. **Permission Setting**: Sets proper file ownership and permissions (775, itential:users)
6. **Reporting**: Generates comprehensive installation report

### Automatic Restart Mechanism

The system uses a checksum annotation that triggers pod restarts when the repositories list changes:

```yaml
# Automatically added to pod template
annotations:
  checksum/repositories: {{ .Values.initAdapterInstaller.repositories | toYaml | sha256sum }}
```

This ensures:
- **Repository changes** → Automatic pod restart
- **No changes** → No unnecessary restarts
- **Version updates** → Selective reinstallation

## File Structure

After installation, adapters are located at:
```
/opt/itential/platform/services/custom/
├── adapter-six_connect/
│   ├── node_modules/
│   ├── .installer-node-major      (Node.js major version used for npm install, for example 22)
│   ├── package.json
│   └── ... (adapter files)
├── adapter-aruba_central/
│   ├── node_modules/
│   ├── .installer-node-major
│   ├── package.json
│   └── ... (adapter files)
└── lost+found/
```

## Lifecycle Management

### Adding a New Adapter

1. Add the repository to the `repositories` list in `values.yaml`
2. Run `helm upgrade`
3. Pod automatically restarts and installs the new adapter

### Updating an Adapter Version

1. Change the `branch` field for the adapter in `values.yaml`
2. Run `helm upgrade`  
3. Pod automatically restarts and reinstalls the adapter with the new version

### Removing an Adapter

1. Remove the repository from the `repositories` list in `values.yaml`
2. Run `helm upgrade`
3. Pod automatically restarts and removes the obsolete adapter

## Logging and Monitoring

### Installation Logs

View initContainer logs:
```bash
kubectl logs <pod-name> -c adapter-installer -n <namespace>
```

### Sample Log Output

```
Installing adapters...
Current user: itential (1001)
Pod hostname: iap-na-0
Running adapter installation on pod-0...
Init container Node.js version: v22.11.0

Cleaning up obsolete adapters...
✅ Keeping existing adapter: adapter-aruba_central
🗑️ Removing obsolete adapter: adapter-old_adapter

=========================================
Processing adapter: adapter-six_connect
Repository URL: https://gitlab.com/itentialopensource/adapters/adapter-six_connect.git
Branch/Tag: v0.8.1
=========================================
✅ Adapter adapter-six_connect already on correct branch/tag: v0.8.1 (commit: 260b1c6c)
⏭️ Skipping adapter-six_connect installation (already up to date)

=========================================
ADAPTER INSTALLATION REPORT
=========================================
Total adapters processed: 2
Successful installations: 2
Failed installations: 0
Obsolete adapters removed: 1

✅ Successfully installed adapters:
  - adapter-six_connect (already installed)
  - adapter-aruba_central (already installed)

🗑️ Removed obsolete adapters:
  - adapter-old_adapter
=========================================
```

When the Node.js major version of the init container changes, an installed adapter is reinstalled even though its commit is unchanged:

```
Init container Node.js version: v22.11.0
Adapter adapter-six_connect already on correct branch/tag: v0.8.1 (commit: 260b1c6c)
adapter-six_connect was installed with Node.js 20, this init container runs Node.js 22, will reinstall
Removing existing adapter-six_connect directory...
Cloning adapter-six_connect repository...
```

## Troubleshooting

### Common Issues

#### 1. initContainer Not Running
**Symptom**: No initContainer logs found
**Cause**: `mountAdapterVolume` is set to `false` or `initAdapterInstaller.enabled` is `false`
**Solution**: 
```yaml
mountAdapterVolume: true
initAdapterInstaller:
  enabled: true
```

#### 2. Permission Errors
**Symptom**: `chmod: operation not permitted` or `chown: operation not permitted`
**Cause**: Security context mismatch or cluster security policies
**Solution**: Ensure `initAdapterInstaller.securityContext` matches `podSecurityContext`:
```yaml
podSecurityContext:
  fsGroup: 1001
  runAsGroup: 1001
  runAsUser: 1001
  runAsNonRoot: true

initAdapterInstaller:
  securityContext:
    runAsUser: 1001
    runAsGroup: 1001
    runAsNonRoot: true
    allowPrivilegeEscalation: false
```

#### 3. Git Clone Failures
**Symptom**: `Failed to clone repository`
**Cause**: Network connectivity, repository access, or invalid URL
**Solution**:
- Verify repository URL is accessible
- Check network policies allow outbound HTTPS
- Ensure the repository exists. For a private repository, set `initAdapterInstaller.gitCredentials.existingSecret` (see [Private Git Repositories](#private-git-repositories)). A wrong or expired token shows up in the init container log as `terminal prompts disabled` or `Authentication failed`
- If the remote cannot be reached (expired token, network problem, git host down) and the adapter is already installed with its dependencies, the installer keeps the installed copy and logs `WARNING: cannot reach the git remote for <adapter>, keeping the installed copy`. The adapter is not updated until the remote is reachable again, so watch for that warning. Without an installed copy the clone fails and is reported as a failed installation

#### 4. NPM Install Failures
**Symptom**: `NPM install failed`
**Cause**: Missing dependencies, network issues, or invalid package.json
**Solution**:
- Check adapter's package.json is valid
- Verify network access to npm registry
- Review specific NPM error messages in logs

#### 5. Adapters Reinstalled After an Upgrade
**Symptom**: Every adapter is reinstalled on the first start after a chart or image change, with `was installed with Node.js unknown` or `was installed with Node.js 20, this init container runs Node.js 22, will reinstall`
**Cause**: The adapters have no `.installer-node-major` file (installed by an older chart version) or were installed with another Node.js major version. This is expected once.
**Solution**:
- No action is needed. The next start skips them, because the marker now matches
- If it happens on every start, check that the volume is writable by the init container user and that `initAdapterInstaller.image.tag` does not change between releases
- If the Platform fails to load an adapter after the reinstall, check the adapter code for APIs removed in Node.js 22 (see [Node.js Version and Native Add-ons](#nodejs-version-and-native-add-ons))

#### 6. Pod Not Restarting After Repository Changes
**Symptom**: Repository changes don't trigger adapter updates
**Cause**: Checksum annotation not working or pod not restarting
**Solution**:
- Verify the checksum annotation is present: `kubectl describe pod <pod-name>`
- Manually restart pod: `kubectl delete pod <pod-name>`

### Debugging Commands

```bash
# Check pod events
kubectl describe pod <pod-name> -n <namespace>

# View initContainer logs
kubectl logs <pod-name> -c adapter-installer -n <namespace>

# Check pod annotations (verify checksum)
kubectl get pod <pod-name> -o yaml | grep -A 5 annotations

# List installed adapters
kubectl exec <pod-name> -n <namespace> -- ls -la /opt/itential/platform/services/custom

# Check adapter permissions
kubectl exec <pod-name> -n <namespace> -- ls -la /opt/itential/platform/services/custom/adapter-*
```

## Best Practices

### Repository Management
- Use specific version tags (e.g., `v1.2.3`) instead of branch names for production
- Keep the repositories list organized and well-documented
- Test adapter compatibility before adding to production

### Version Control
- Use semantic versioning for adapter releases
- Tag releases properly in Git repositories
- Maintain compatibility between adapter versions and IAP versions

### Security
- Run initContainer as non-root user
- Use least-privilege security contexts
- Regularly update the `node` base image, and pin `initAdapterInstaller.image.tag` to the Node.js major version of your Platform image

### Monitoring
- Monitor initContainer logs for failures
- Set up alerts for failed adapter installations
- Track adapter installation metrics

## Architecture Details

### Components

1. **StatefulSet Template**: Generates repository arrays and mounts ConfigMap
2. **ConfigMap Template**: Contains the complete installation script
3. **Init Container**: Executes the installation script with repository arrays
4. **Persistent Volume**: Shared storage between initContainer and main container

### Template Files

- `templates/statefulset.yaml`: InitContainer definition and repository array generation
- `templates/configmap-adapter-installer.yaml`: Installation script logic
- `values.yaml`: Configuration schema and defaults

### Security Model

- Non-root execution (user/group 1001)
- Read-only ConfigMap mounting
- Shared persistent volume with main container
- Restricted security context with no privilege escalation

## Migration Guide

### From Inline Script to ConfigMap

If migrating from an older version with inline scripts in values:

1. Remove any `script` field from `initAdapterInstaller`
2. Ensure only `repositories` field is configured
3. The system automatically uses the ConfigMap-based script

### Upgrading Helm Chart

When upgrading the Helm chart:
1. Existing adapter installations are preserved
2. Only changed adapters are reinstalled
3. ConfigMap changes trigger automatic pod restarts

**Upgrading to chart 1.13.0 (Node.js aware installer).** The installer now records the Node.js major version of each adapter, and the init container image follows the Platform version when `initAdapterInstaller.image.tag` is empty: `node:22-bullseye` for Platform 6.6.0 and later, `node:18-bullseye` (unchanged) for earlier releases. A Platform below 6.6.0 therefore keeps its init image, and nothing moves to Node.js 22 unless you set the tag. Adapters installed by an earlier chart version have no record, so **every adapter is reinstalled once** (cloned again and `npm install` run) on the first start after the upgrade, even if its branch or tag did not change. Plan for a longer first start of the pod, and make sure the git server and npm registry are reachable. After that, adapters are skipped again until the Node.js major version changes. When you upgrade the Platform image to 6.6.0 or later, the init image moves to Node.js 22 and the adapters are rebuilt once on the next start. To pin a version, set `initAdapterInstaller.image.tag` (for example `20-bullseye`).

## Support

For issues related to the adapter installer:

1. Check this documentation for common issues
2. Review initContainer logs for specific error messages
3. Verify configuration matches the examples provided
4. Test with a minimal configuration first

## Examples

### Production Configuration
```yaml
mountAdapterVolume: true

initAdapterInstaller:
  enabled: true
  image:
    repository: node
    tag: "22-bullseye"       # Pinned to the Node.js version of the Platform image (22 for 6.6.0 and later)
    pullPolicy: IfNotPresent
  securityContext:
    runAsUser: 1001
    runAsGroup: 1001
    runAsNonRoot: true
    allowPrivilegeEscalation: false
  repositories:
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-six_connect.git"
      branch: "v0.8.1"
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-aruba_central.git"
      branch: "v2.3.0"
    - url: "https://gitlab.com/itentialopensource/adapters/adapter-github.git"
      branch: "v1.5.2"
```

### Development Configuration
```yaml
mountAdapterVolume: true

initAdapterInstaller:
  enabled: true
  repositories:
    # Use default branches for development
    - "https://gitlab.com/itentialopensource/adapters/adapter-six_connect.git"
    - "https://gitlab.com/itentialopensource/adapters/adapter-aruba_central.git"
```