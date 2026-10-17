{{/*
Shared names and labels.

app.kubernetes.io/name stays "items-api" on purpose: the ServiceMonitor, NetworkPolicy and
scripts all select on it. app.kubernetes.io/instance (the release name) keeps two releases
in one namespace from selecting each other's pods.
*/}}
{{- define "items-api.fullname" -}}
{{- if eq .Release.Name .Chart.Name -}}{{ .Chart.Name }}{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "items-api.selectorLabels" -}}
app.kubernetes.io/name: items-api
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "items-api.labels" -}}
{{ include "items-api.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: autoscale-platform
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
