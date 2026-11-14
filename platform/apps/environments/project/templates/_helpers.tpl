{{/* The project's environments: each one's namespace suffix, and the host an Ingress in it may
claim (host), or the domain whose names it may claim (suffix). */}}
{{- define "project.environments" -}}
{{- $domain := required "domain is required" .Values.domain -}}
- {env: dev, host: {{ printf "dev.%s" $domain | quote }}}
- {env: prod, host: {{ $domain | quote }}}
{{- if .Values.previews }}
- {env: preview, suffix: {{ printf "qa.%s" $domain | quote }}}
{{- end }}
{{- end -}}
