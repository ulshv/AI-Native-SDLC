#!/usr/bin/env bash
# The manifests Argo CD applies from this repository, rendered and checked before they get there
# (CI's `manifests` job runs `check`):
#   platform/manifests.sh render   one YAML stream: the Applications, every manifest of a directory
#                                  source, the orchestrator's chart and the project files' ConfigMap
#   platform/manifests.sh check    the stream through kubeconform, strict, against Kubernetes'
#                                  schemas and the CRDs' (Argo CD, cert-manager, CloudNativePG); and
#                                  the project files against project.schema.json
# The external charts (Argo CD, Gitea, Traefik, cert-manager, CloudNativePG, Plane) are not
# rendered: their values files are not manifests, and a chart takes unknown values silently, so
# rendering them would cost a download per chart for little. Argo CD's sync reports what is left.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

read -ra KUSTOMIZE <<<"${KUSTOMIZE:-kustomize build}" # `kubectl kustomize` works too
KUBECONFORM=${KUBECONFORM:-kubeconform}
# The cluster's Kubernetes, from the k3s version the bootstrap installs.
K8S_VERSION=$(sed -n 's/^K3S_VERSION=v\([0-9.]*\)+.*/\1/p' platform/bootstrap.sh)
# CRD schemas, at a fixed commit of the catalog.
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/89c736bdde38b5fecd0f8436ec5f3aa3679a3b86/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
CHECK_JSONSCHEMA=check-jsonschema==0.39.0

# Files of an app's directory that are not manifests: its Application, a chart's values, a
# kustomization (Plane's, whose own manifests are checked as they are).
not_a_manifest() { [[ $1 =~ /(app|values|kustomization)\.yaml$ ]]; }

render() {
  local files=(platform/argocd/root.yaml platform/apps/*/app.yaml) file
  for file in platform/apps/*/*.yaml; do
    not_a_manifest "$file" || files+=("$file")
  done
  for file in "${files[@]}"; do
    printf -- '---\n# %s\n' "$file"
    cat "$file"
  done
  printf -- '---\n'
  helm template orchestrator platform/apps/orchestrator/chart --namespace sdlc --set image.tag=check
  printf -- '---\n'
  "${KUSTOMIZE[@]}" platform/projects
}

check() {
  render | $KUBECONFORM -strict -summary -kubernetes-version "$K8S_VERSION" \
    -schema-location default -schema-location "$CRDS" -
  uvx --from "$CHECK_JSONSCHEMA" check-jsonschema --schemafile platform/projects/project.schema.json \
    platform/projects/*/project.yaml
}

case ${1:-} in
  render) render ;;
  check) check ;;
  *) echo "usage: $0 render|check" >&2 && exit 2 ;;
esac
