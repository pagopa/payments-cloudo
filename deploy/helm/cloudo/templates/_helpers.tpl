{{/*
Expand the name of the chart.
*/}}
{{- define "cloudo.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
*/}}
{{- define "cloudo.fullname" -}}
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
Namespace
*/}}
{{- define "cloudo.namespace" -}}
{{- .Values.global.namespace }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "cloudo.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "cloudo.labels" -}}
helm.sh/chart: {{ include "cloudo.chart" . }}
{{ include "cloudo.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "cloudo.selectorLabels" -}}
app.kubernetes.io/name: {{ include "cloudo.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Return the name of the Secret to use for application secrets.
Uses existingSecretName when provided, otherwise the chart-managed "cloudo-secrets".
*/}}
{{- define "cloudo.secretName" -}}
{{- if .Values.existingSecretName -}}
{{- .Values.existingSecretName -}}
{{- else -}}
cloudo-secrets
{{- end -}}
{{- end -}}

{{/*
Return the ServiceAccount name.
*/}}
{{- define "cloudo.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
  {{- default (include "cloudo.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
  {{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Orchestrator image
*/}}
{{- define "cloudo.orchestratorImage" -}}
{{ .Values.image.registry }}/cloudo-orchestrator:{{ .Values.image.tag }}
{{- end }}

{{/*
Worker image
*/}}
{{- define "cloudo.workerImage" -}}
{{ .Values.image.registry }}/cloudo-worker:{{ .Values.image.tag }}
{{- end }}

{{/*
UI image
*/}}
{{- define "cloudo.uiImage" -}}
{{ .Values.image.registry }}/cloudo-ui:{{ .Values.image.tag }}
{{- end }}

{{/*
Agent image
*/}}
{{- define "cloudo.agentImage" -}}
{{ .Values.image.registry }}/cloudo-agent:{{ .Values.image.tag }}
{{- end }}
