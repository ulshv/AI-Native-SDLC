{{- define "orchestrator.image" -}}
{{ .Values.image.repository }}:{{ required "image.tag is required: the commit" .Values.image.tag }}
{{- end }}
{{- define "orchestrator.labels" -}}
app.kubernetes.io/name: orchestrator
app.kubernetes.io/part-of: delivery-loop
{{- end }}
{{- define "orchestrator.database" -}}
- name: DATABASE_URL
  valueFrom: {secretKeyRef: {name: orchestrator-env, key: DATABASE_URL}}
{{- end }}
