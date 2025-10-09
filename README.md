# Helm chart for Itential Automation Platform

This repo contains helm charts for running Itential Automation Platform in Kubernetes. Requires 
Helm version `v3.15.0`.

## Itential Automation Platform (IAP)

The chart will not install the Redis and MongoDB dependencies of the IAP application. The chart
assumes that those are running, configured, and bootstrapped with all necessary data. The
application is installed using a Kubernetes Statefulset. It also includes persistent volume claims,
ingress, and other Kubernetes objects suitable to run the application.

This chart is optimized for Itential version P6 and beyond and will not work with older Itential
versions.

### Usage

This will install IAP according to how its configured in the values.yaml file ("latest").

```bash
helm install iap . -f values.yaml
```

This will install IAP with the "6.0.4" image.

```bash
helm install iap . -f values.yaml --set image.tag=6.0.4
```

### Requirements & Dependencies

| Repository | Name | Version |
|:-----------|:-----|:--------|
| https://charts.jetstack.io | cert-manager | 1.12.3 |
| https://kubernetes-sigs.github.io/external-dns/ | external-dns | 1.17.0 |

#### Secrets

The chart assumes the following secrets, they are not included in the Chart.

##### imagePullSecrets

This is the secret that will pull the image from the Itential ECR. Name to be determined by the user
 of the chart and that name must be provided in the values file (`imagePullSecrets[0].name`).

##### itential-platform-secrets

This secret contains several sensitive values that the application may use. They are loaded into the
pod as environment variables. Some are optional and depend on your implementation. The creation of
this secret is left out of the chart to allow for flexibility with its creation.

| Secret Key | Description | Required? |
|:-----------|:------------|:----------|
| ITENTIAL_DEFAULT_USER_PASSWORD | The password for the default local user, normally only used during installation and initial configuration. | Yes |
| ITENTIAL_ENCRYPTION_KEY | Used by the application for native encryption of sensitive values. 64-length hex string describing a 256 bit encryption key. | Yes |
| ITENTIAL_MONGO_PASSWORD | The MongoDB password for the `itential` user. | Yes |
| ITENTIAL_MONGO_URL | The MongoDB connection string, which can sometimes contain passwords. | Yes |
| ITENTIAL_REDIS_PASSWORD | The Redis password for the `itential` user. | Yes |
| ITENTIAL_REDIS_SENTINEL_PASSWORD | The Redis Sentinel password for the `itential` user. Only required if connecting to a Redis replica set that also uses Redis Sentinel. | No |
| ITENTIAL_VAULT_SECRET_ID | The Hashicorp Vault Secret ID when using Hashicorp Vault as the secrets manager. Only required if using Hashicorp Vault for secrets. | No |
| ITENTIAL_WEBSERVER_HTTPS_PASSPHRASE | The passphrase used in conjunction with an HTTPS certificate. | No |

##### iap-ca

This secret represents the CA used by cert-manager to derive all the TLS certificates. Name to be
provided by the user of the chart in the values file (`issuer.caSecretName`) if using cert-manager.

#### Certificates

The chart will require a Certificate Authority to be added to the Kubernetes environment. This is
used by the chart when running with `useTLS` flag enabled. The chart will use this CA to generate
the necessary certificates using a Kubernetes `Issuer` which is included. The Issuer will issue the
certificates using the CA. The certificates are then included using a Kubernetes `Secret` which is
mounted by the pods. Creating and adding this CA is outside of the scope of this chart.

Both the `Issuer` and the `Certificate` objects are realized by using the widely used Kubernetes
add-on called `cert-manager`. Cert-manager is responsible for making the TLS certificates required
by using the CA that was installed separately. The installation of cert-manager is outside the scope
of this chart. To check if this is already installed run this command:

```bash
kubectl get crds | grep cert-manager
```

For more information see the [Cert Manager project](https://cert-manager.io/).

If `cert-manager` can not be used then the TLS certificates must be manually added to the Kubernetes
cluster. The Helm templates expect them to be in a secret named `<Chart.name>-tls-secret`. It
expects the following keys:

| Key | Description |
|:----|:------------|
| tls.crt | The TLS certificate that identifies this "server". |
| tls.key | The private key for this certificate. |
| ca.crt | The Certificate Authority used to generate these certificates and keys. |

#### DNS

This is an optional requirement.

Itential used the ExternalDNS project to facilitate the creation of DNS entries. ExternalDNS
synchronizes exposed Kubernetes Services and Ingresses with DNS providers. This is not a
requirement for running the chart. The chart and the application can run without this if needed. We
chose to use this to provide us with external addresses.

For more information see the [ExternalDNS project](https://github.com/kubernetes-sigs/external-dns).

### Volumes

| Name | Type | Description |
|:-----|:-----|:------------|
| iap-logs-volume | Persistent Volume Claim | A persistent volume claim to mount a directory to write IAP log files to |
| iap-asset-volume | Persistent Volume Claim | A persistent volume claim to mount a directory that includes adapters and apps |

### How to construct the iap-asset-volume

This volume is intended to store the applications and adapters unique to a customer. Its contents
will reflect a customer's unique usage of IAP and contain all of the adapters and custom
applications required. There is an expectation in the container of the structure of the files in
this volume. All adapters and applications can be added into the same parent directory that will
then be mounted in the container.

```bash
.
├── adapter1/
├── adapter2/
├── custom-application1/
└── custom-application2/
```

This will be correctly translated inside the container to the appropriate directories for IAP to
understand.

## Values

| Key | Type | Default | Description |
|:----|:-----|:--------|:------------|
| affinity | object | `{}` | Additional affinities |
| applicationPort | int | `3443` | The port that the application will run on |
| certManager.enabled | bool | `true` | Toggles the use of cert-manager for managing the TLS certificates. Setting this to false means that creation of the TLS certificates will be manual and outside of the chart. |
| certificate.commonName | string | `"iap.example.com"` | The Common Name to use when creating certificates |
| certificate.domain | string | `"example.com"` | The domain to use when creating certificates. This will be used by the templates to build a complete list of hosts to enable direct access to individual pods. Some UI actions in Itential require direct access to pods. |
| certificate.duration | string | `"2160h"` | Specifies how long the certificate should be valid for (its lifetime). |
| certificate.enabled | bool | `false` | Toggle to use the certificate object or not |
| certificate.issuerRef.kind | string | `"Issuer"` | The issuer type |
| certificate.issuerRef.name | string | `"iap-ca-issuer"` | The name of the issuer with the CA reference. |
| certificate.renewBefore | string | `"48h"` | Specifies how long before the certificate expires that cert-manager should try to renew. |
| env.ITENTIAL_MONGO_AUTH_ENABLED | string | `"true"` | Instructs the MongoDB driver to use the provided user/password when connecting to MongoDB. |
| env.ITENTIAL_MONGO_DB_NAME | string | `"itential"` | The name of the MongoDB logical database to connect to. |
| env.ITENTIAL_MONGO_TLS_ALLOW_INVALID_CERTIFICATES | string | `"false"` | If true, disables the validation checks for TLS certificates on other servers in the cluster and allows the use of invalid or self-signed certificates to connect. |
| env.ITENTIAL_MONGO_TLS_ENABLED | string | `"false"` | Instruct the MongoDB driver to use TLS protocols when connecting to the database. |
| env.ITENTIAL_MONGO_USER | string | `"itential"` | The username to use when connecting to MongoDB. |
| env.ITENTIAL_REDIS_HOST | string | `"redis.example.com"` | The hostname of the Redis server. Not used when connecting to Redis Sentinels. |
| env.ITENTIAL_REDIS_PORT | string | `"6379"` | The port to use when connecting to this Redis instance. |
| env.ITENTIAL_REDIS_USERNAME | string | `"itential"` | The username to use when connecting to Redis. |
| external-dns.enabled | bool | `false` | Optional dependency to generate a static external DNS name |
| image.pullPolicy | string | `"IfNotPresent"` | The image pull policy |
| image.repository | string | `"497639811223.dkr.ecr.us-east-2.amazonaws.com/itential-platform"` | The image repository |
| image.tag | string | `nil` | The image tag |
| imagePullSecrets | list | `[]` | The secrets object used to pull the image from the repo |
| ingress.annotations | object | `{"alb.ingress.kubernetes.io/backend-protocol":"HTTPS","alb.ingress.kubernetes.io/healthcheck-interval-seconds":"15","alb.ingress.kubernetes.io/healthcheck-path":"/health/status","alb.ingress.kubernetes.io/healthcheck-port":"3443","alb.ingress.kubernetes.io/healthcheck-protocol":"HTTPS","alb.ingress.kubernetes.io/healthcheck-timeout-seconds":"5","alb.ingress.kubernetes.io/healthy-threshold-count":"2","alb.ingress.kubernetes.io/listen-ports":"[{\"HTTPS\": 443}]","alb.ingress.kubernetes.io/load-balancer-attributes":"idle_timeout.timeout_seconds=60","alb.ingress.kubernetes.io/load-balancer-name":"itential-iap-lb","alb.ingress.kubernetes.io/scheme":"internet-facing","alb.ingress.kubernetes.io/success-codes":"200","alb.ingress.kubernetes.io/target-type":"ip","alb.ingress.kubernetes.io/unhealthy-threshold-count":"2"}` | The annotations for this ingress object. These are passed into the template as is and will render as you see here. Itential leveraged AWS ALB but others should work. |
| ingress.className | string | `"alb"` | The ingress controller class name |
| ingress.directAccess.baseDomain | string | `"pet-sbx.itential.io"` | The base domain for each Itential pod, used by the templates to create host names. |
| ingress.directAccess.enabled | bool | `true` | Enable direct access to all Itential pods. |
| ingress.directAccess.path | string | `"/"` | The path |
| ingress.enabled | bool | `true` | The ingress object can be disabled and will not be created with this set to false |
| ingress.loadBalancer.enabled | bool | `true` | Enable a load balancer that will distribute request to all Itential pods |
| ingress.loadBalancer.host | string | `"iap.pet-sbx.itential.io"` | The Load balancer host name |
| ingress.loadBalancer.path | string | `"/"` | The path |
| ingress.name | string | `"iap-ingress"` | The name of this Kubernetes ingress object |
| ingress.pathType | string | `"Prefix"` | The ingress controller path type |
| issuer.caSecretName | string | `nil` | The CA secret to be used by this issuer when creating TLS certificates. |
| issuer.enabled | bool | `true` | Toggle to use the issuer object or not |
| issuer.name | string | `"iap-ca-issuer"` | The name of this issuer. |
| mountAdapterVolume | bool | `false` | Toggle a volume mount which contains adapter code. When this is set to false it is assumed that all adapters will be layered into the Itential provided container. |
| mountLogVolume | bool | `false` | Toggle a volume with will contain log files. Not required if log data is being captured from Stdout. |
| nodeSelector | object | `{}` | Additional nodeSelectors |
| persistentVolumeClaims.assetClaim | object | `{"storage":"10Gi"}` | This represents the claim for the persistence for the adapters and other custom applications that may have been developed by the customer. |
| persistentVolumeClaims.assetClaim.storage | string | `"10Gi"` | The requested amount of storage |
| persistentVolumeClaims.enabled | bool | `true` | Toggle the use of persistentVolumeClaims |
| persistentVolumeClaims.logClaim | object | `{"storage":"10Gi"}` | This represents the claim for the persistence for the log files created and written to by the IAP application. |
| persistentVolumeClaims.logClaim.storage | string | `"10Gi"` | The requested amount of storage |
| podAnnotations | object | `{}` | Additional pod annotations |
| podLabels | object | `{}` | Additional pod labels |
| podSecurityContext | object | `{"fsGroup":1001,"runAsNonRoot":true,"runAsUser":1001}` | Additional pod security context. The pods will mount some persistent volumes. These settings allow for that to happen. |
| processExporter.enabled | bool | `true` | Toggle to enable the process exporter sidecar container |
| processExporter.image.repository | string | `"ncabatoff/process-exporter"` | The process exporter image repository |
| processExporter.image.tag | string | `"latest"` | The process exporter image tag |
| processExporter.port | int | `9256` | Process exporter metrics port |
| replicaCount | int | `2` | The number of pods to start |
| securityContext | object | `{}` | Additional security context |
| service.name | string | `"iap-service"` | The name of this Kubernetes service object. |
| service.port | int | `443` | The port that this service object is listening on. |
| service.type | string | `"ClusterIP"` | The service type. |
| storageClass.enabled | bool | `true` | Toggle the use of storageClass |
| storageClass.name | string | `"iap-ebs-gp3"` | The name of the storageClass |
| storageClass.parameters | string | `nil` | Key-value pairs passed to the provisioner |
| storageClass.provisioner | string | `""` | Specifies which volume plugin provisions the storage |
| storageClass.reclaimPolicy | string | `"Retain"` | What happens to PersistentVolumes when released. Itential recommends "retain". |
| storageClass.volumeBindingMode | string | `"WaitForFirstConsumer"` | Controls when volume binding occurs |
| tolerations | list | `[]` | Additional tolerations |
| useTLS | bool | `true` | Toggle to enable TLS features and configuration. |
| volumeMounts | list | `[]` | Additional volumeMounts to output on the Statefulset definition. |
| volumes | list | `[]` | Additional volumes to output on the Statefulset definition. |
