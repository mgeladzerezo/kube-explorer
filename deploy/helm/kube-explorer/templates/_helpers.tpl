{{- define "kube-explorer.name" -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}

{{- define "kube-explorer.labels" -}}
app.kubernetes.io/name: kube-explorer
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "kube-explorer.selector" -}}
app.kubernetes.io/name: kube-explorer
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "kube-explorer.backendImage" -}}
{{ .Values.image.backend.repository }}:{{ .Values.image.backend.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "kube-explorer.backendEnv" -}}
- name: KUBE_MODE
  value: in-cluster
{{- range $name, $value := .Values.backend.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secret.name }}
      key: DATABASE_URL
{{- end -}}

{{- define "kube-explorer.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}
