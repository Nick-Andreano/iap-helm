{{/*
Expand the name of the chart.
*/}}
{{- define "iap.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "iap.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "iap.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "iap.labels" -}}
helm.sh/chart: {{ include "iap.chart" . }}
{{ include "iap.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: "itential-platform"
app.kubernetes.io/layer: "application"
{{- end }}

{{/*
Selector labels
*/}}
{{- define "iap.selectorLabels" -}}
app.kubernetes.io/name: {{ include "iap.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Common annotations.
*/}}
{{- define "iap.annotations" -}}
itential.com/copyright: "Copyright (c) {{ now | date "2006" }}, Itential, Inc."
itential.com/license: "GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)"
helm.sh/template-file: "{{ $.Template.Name }}"
{{- end -}}

{{/*
Compute the effective ServiceAccount name for pods and the SA resource.
When create is true and name is empty, falls back to the fullname so the
created SA and the pod spec always reference the same name.
When create is false and name is empty, returns "" so Kubernetes uses the
namespace default SA.
*/}}
{{- define "iap.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default (include "iap.fullname" .) -}}
{{- else -}}
{{- .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{/*
Direct host names
*/}}
{{- define "iap.DirectAccessHost" -}}
{{- $iterator := .iterator -}}
{{- if .Values.ingress.directAccess.hostOverride -}}
{{- printf "%s-%d.%s" .Values.ingress.directAccess.hostOverride $iterator .Values.ingress.directAccess.baseDomain -}}
{{- else -}}
{{- printf "%s-%s-%d.%s" (include "iap.fullname" .) .Release.Namespace $iterator .Values.ingress.directAccess.baseDomain -}}
{{- end -}}
{{- end }}

{{/*
Generate the hostname for the job-metrics-exporter ingress rule.
Follows the same {fullname}-{namespace}-job-metrics.{baseDomain} convention as DirectAccessHost.
*/}}
{{- define "iap.jobMetricsExporterHost" -}}
{{- printf "%s-%s-job-metrics.%s" (include "iap.fullname" .) .Release.Namespace .Values.ingress.directAccess.baseDomain -}}
{{- end }}

{{/*
Generate the full list of TLS hostnames for the ingress spec.
Includes the load balancer hostname and one entry per replica for direct access.
Rendered as a YAML list of quoted strings, suitable for use with nindent.
*/}}
{{- define "iap.ingressTLSHosts" -}}
{{- if .Values.ingress.loadBalancer.enabled }}
- {{ .Values.ingress.loadBalancer.host | quote }}
{{- end }}
{{- if .Values.ingress.directAccess.enabled }}
{{- range $i := until (.Values.replicaCount | int) }}
- {{ include "iap.DirectAccessHost" (dict "Values" $.Values "Release" $.Release "Chart" $.Chart "Template" $.Template "iterator" $i) | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Tag of the adapter installer init container image. An explicit initAdapterInstaller.image.tag wins.
Otherwise the Node.js version follows the Platform image tag: 22 for Platform 6.6.0 and later (and for
tags that are not a version, such as latest), 18 (the previous default) for earlier releases.
*/}}
{{- define "iap.adapterInstallerImageTag" -}}
{{- if .Values.initAdapterInstaller.image.tag -}}
{{- .Values.initAdapterInstaller.image.tag -}}
{{- else -}}
{{- $version := regexFind "^v?[0-9]+\\.[0-9]+(\\.[0-9]+)?" (.Values.image.tag | toString) -}}
{{- if and $version (not (regexMatch "^v?[0-9]+\\.[0-9]+\\.[0-9]+" $version)) -}}
{{- $version = printf "%s.0" $version -}}
{{- end -}}
{{- if and $version (semverCompare "<6.6.0" $version) -}}
18-bullseye
{{- else -}}
22-bullseye
{{- end -}}
{{- end -}}
{{- end }}
