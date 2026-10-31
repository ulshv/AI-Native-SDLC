#!/usr/bin/env bash
# Platform smoke checks.
#   platform/smoke.sh          run every registered check
#   platform/smoke.sh <name>   run the named checks only
# Stops at the first failing check and exits non-zero.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

SECRETS_FILE=${SECRETS_DIR:-$HOME/.config/ai-native-sdlc}/secrets.env
# The checks always talk to this cluster, whatever KUBECONFIG the shell had (as bootstrap.sh).
export KUBECONFIG=$HOME/.kube/config

CHECKS=()
# A check is a function check_<name>, registered with `register <name>`.
register() { CHECKS+=("$1"); }

# Every non-empty value in secrets.env, one per line. Never print it.
secret_values() { sed -n 's/^[A-Za-z0-9_]*=//p' "$SECRETS_FILE" | grep -v '^$'; }

# Checks report file names only, never the matched value.
register no_secret_values_in_git
check_no_secret_values_in_git() {
  [[ -s $SECRETS_FILE ]] || { echo "no $SECRETS_FILE"; return 1; }
  local hits
  hits=$(git ls-files -z | xargs -0 grep -lF -f <(secret_values) -- 2>/dev/null || true)
  [[ -z $hits ]] || { echo "tracked files contain a value from secrets.env:"; echo "$hits"; return 1; }
}

register no_secret_material_in_worktree
check_no_secret_material_in_worktree() {
  [[ -s $SECRETS_FILE ]] || { echo "no $SECRETS_FILE"; return 1; }
  local hits
  hits=$(
    {
      find . -path ./.git -prune -o \( -name secrets.env -o -name '*.key' -o -name .secrets \) -print
      grep -rlE --exclude-dir=.git -e '-----BEGIN ([A-Z]+ )?PRIVATE KEY-----' . || true
      grep -rlF --exclude-dir=.git -f <(secret_values) . || true
    } 2>/dev/null | sort -u
  )
  [[ -z $hits ]] || { echo "secret material in the working tree:"; echo "$hits"; return 1; }
}

# run_pod <namespace> <name> <kubectl run args...>: runs a throwaway pod to completion, prints
# its output, deletes it, and fails when the pod failed. It waits for the pod instead of
# attaching to it, because a short-lived container can exit before kubectl attaches.
run_pod() {
  local ns=$1 pod=$2 phase=""
  shift 2
  kubectl delete pod "$pod" -n "$ns" --ignore-not-found >/dev/null
  kubectl run "$pod" -n "$ns" --restart=Never "$@" >/dev/null || return 1
  for _ in $(seq 60); do
    phase=$(kubectl get pod "$pod" -n "$ns" -o jsonpath='{.status.phase}')
    [[ $phase == Succeeded || $phase == Failed ]] && break
    sleep 1
  done
  kubectl logs "$pod" -n "$ns" 2>/dev/null
  kubectl delete pod "$pod" -n "$ns" --wait=false >/dev/null
  [[ $phase == Succeeded ]] || { [[ $phase == Failed ]] || echo "pod $ns/$pod did not finish (phase ${phase:-unknown})"; return 1; }
}

# A pod resolves a Service name through the cluster DNS Service. Fails when something on the
# host, such as a VPN in TUN mode, steers pods' DNS traffic away from CoreDNS.
register pod_dns
check_pod_dns() {
  local want
  want=$(kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}') || return 1
  run_pod default smoke-pod-dns \
    --image=docker.io/rancher/mirrored-library-busybox:1.37.0 --image-pull-policy=IfNotPresent -- \
    sh -c "nslookup -timeout=3 kubernetes.default.svc.cluster.local | grep -q 'Address: $want' ||
      { echo 'kubernetes.default.svc.cluster.local does not resolve to $want from a pod'; exit 1; }"
}

# Runs psql in a throwaway pod with the gitea role's password from its Secret, so the password
# never appears on a command line.
register database_isolation
check_database_isolation() {
  local image pod=smoke-database-isolation
  image=$(kubectl get cluster platform-pg -n databases -o jsonpath='{.spec.imageName}') || return 1
  run_pod databases "$pod" --image="$image" --overrides="$(
    jq -n --arg pod "$pod" --arg image "$image" '{spec: {containers: [{
      name: $pod, image: $image,
      env: [{name: "PGPASSWORD", valueFrom: {secretKeyRef: {name: "pg-gitea", key: "password"}}}],
      command: ["sh", "-c", "
        psql \"host=platform-pg-rw user=gitea dbname=gitea\" -tAc \"select 1\" >/dev/null ||
          { echo \"gitea cannot open its own database\"; exit 1; }
        if psql \"host=platform-pg-rw user=gitea dbname=plane\" -tAc \"select 1\" 2>/tmp/err; then
          echo \"gitea opened the plane database\"; exit 1
        fi
        grep -q \"pg_hba.conf rejects connection\" /tmp/err || { cat /tmp/err; exit 1; }"]
    }]}}'
  )"
}

# with_gitea <command...>: runs the command with GITEA_URL pointing at a temporary port-forward
# to Gitea's Service, so it works before the ingress exists.
with_gitea() {
  local log pid port rc
  log=$(mktemp)
  kubectl port-forward -n gitea svc/gitea-http 0:3000 >"$log" 2>&1 &
  pid=$!
  for _ in $(seq 50); do
    port=$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\).*/\1/p' "$log")
    [[ -z $port ]] || break
    sleep 0.2
  done
  if [[ -z $port ]]; then
    cat "$log"
    kill "$pid" 2>/dev/null
    rm -f "$log"
    return 1
  fi
  GITEA_URL=http://127.0.0.1:$port "$@"
  rc=$?
  kill "$pid" 2>/dev/null
  rm -f "$log"
  return $rc
}

# The admin signs in through the API with the password from secrets.env. The password goes
# to curl through a netrc on a pipe, never as an argument.
gitea_admin_is_admin() {
  local password
  password=$(sed -n 's/^GITEA_ADMIN_PASSWORD=//p' "$SECRETS_FILE")
  curl -fsS --max-time 10 --netrc-file <(printf 'machine 127.0.0.1 login gitea_admin password %s\n' "$password") \
    "$GITEA_URL/api/v1/user" | jq -e '.login == "gitea_admin" and .is_admin' >/dev/null ||
    { echo "gitea_admin cannot sign in as an admin"; return 1; }
}

register gitea_admin_login
check_gitea_admin_login() { with_gitea gitea_admin_is_admin; }

# gitea_main_head: the commit main points to on Gitea (runs inside with_gitea).
gitea_main_head() {
  local password
  password=$(sed -n 's/^GITEA_ADMIN_PASSWORD=//p' "$SECRETS_FILE")
  curl -fsS --max-time 10 --netrc-file <(printf 'machine 127.0.0.1 login gitea_admin password %s\n' "$password") \
    "$GITEA_URL/api/v1/repos/sdlc/ai-native-sdlc/branches/main" | jq -er .commit.id
}

# apps_synced_at <commit>: every Application is Synced and Healthy at that commit. A
# multi-source Application lists the commit among its revisions.
apps_synced_at() {
  kubectl get applications -n argocd -o json | jq -e --arg head "$1" '
    (.items | length) > 0 and all(.items[];
      .status.sync.status == "Synced" and .status.health.status == "Healthy" and
      (.status.sync.revision == $head or ((.status.sync.revisions // []) | index($head)) != null))' >/dev/null
}

# A change pushed to main is applied within 5 minutes: run it after a push.
register gitops_synced_to_main
check_gitops_synced_to_main() {
  local head
  head=$(with_gitea gitea_main_head) || return 1
  for _ in $(seq 30); do
    apps_synced_at "$head" && return 0
    sleep 10
  done
  echo "not every Application is Synced and Healthy at main ${head:0:7} after 5 minutes:"
  kubectl get applications -n argocd
  return 1
}

# A manual change to a managed object is reverted within 5 minutes.
register gitops_self_heal
check_gitops_self_heal() {
  kubectl patch configmap self-heal-probe -n smoke --type merge -p '{"data": {"value": "changed-by-smoke"}}' >/dev/null ||
    return 1
  for _ in $(seq 60); do
    [[ $(kubectl get configmap self-heal-probe -n smoke -o jsonpath='{.data.value}') == declared-in-git ]] && return 0
    sleep 5
  done
  echo "Argo CD did not revert smoke/self-heal-probe within 5 minutes"
  return 1
}

# Argo CD manages itself: every setting in its values.yaml is live in its own ConfigMaps.
register argocd_self_managed
check_argocd_self_managed() {
  local values
  values=$(yq -o json '.configs' <platform/apps/argocd/values.yaml) || return 1
  jq -en --argjson want "$values" \
    --argjson cm "$(kubectl get configmap argocd-cm -n argocd -o json)" \
    --argjson params "$(kubectl get configmap argocd-cmd-params-cm -n argocd -o json)" '
    def same($want; $live): all($want | to_entries[];
      ($live[.key] // "" | tostring | rtrimstr("\n")) == (.value | tostring | rtrimstr("\n")));
    same($want.cm; $cm.data) and same($want.params; $params.data)' >/dev/null ||
    { echo "argocd-cm or argocd-cmd-params-cm differs from platform/apps/argocd/values.yaml"; return 1; }
}

# Every container image names a tag or a digest, and no tag is latest.
register no_floating_images
check_no_floating_images() {
  local floating
  floating=$(kubectl get pods -A -o json |
    jq -r '.items[] | .metadata.namespace + "/" + .metadata.name + " " + ((.spec.initContainers // []) + .spec.containers)[].image' |
    awk '{ image = $2; sub(/.*\//, "", image) }
      $2 !~ /@sha256:/ && (image !~ /:/ || image ~ /:latest$/) { print }')
  [[ -z $floating ]] || { echo "images without a pinned version:"; echo "$floating"; return 1; }
}

service_names() { grep -vE '^[[:space:]]*(#|$)' platform/hosts.txt | sed 's/$/.sdlc.internal/'; }

# Every service name resolves to 127.0.0.1 on the host, and to Traefik's Service from a pod.
register names_resolve
check_names_resolve() {
  local name traefik pod=smoke-names-resolve failed=0
  for name in $(service_names); do
    [[ $(getent hosts "$name" | awk '{ print $1 }') == 127.0.0.1 ]] ||
      { echo "$name does not resolve to 127.0.0.1 on the host"; failed=1; }
  done
  traefik=$(kubectl get svc traefik -n traefik -o jsonpath='{.spec.clusterIP}') || return 1
  run_pod default "$pod" \
    --image=docker.io/rancher/mirrored-library-busybox:1.37.0 --image-pull-policy=IfNotPresent -- \
    sh -c "failed=0; for name in $(service_names | paste -sd' '); do
      nslookup -timeout=3 \$name | grep -q 'Address: $traefik' ||
        { echo \"\$name does not resolve to Traefik ($traefik) from a pod\"; failed=1; }
    done; exit \$failed" || failed=1
  return $failed
}

# Every service name answers over HTTPS with a certificate the host trusts (curl without -k),
# and plain HTTP redirects to HTTPS. A name whose service is not deployed yet gets Traefik's
# 404, which still proves the name, the route to Traefik and the certificate.
register https_on_every_name
check_https_on_every_name() {
  local name code location failed=0
  for name in $(service_names); do
    code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "https://$name/") &&
      ((code < 500)) || { echo "https://$name/ failed (HTTP $code)"; failed=1; }
    read -r code location < <(curl -sS --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' "http://$name/")
    [[ $code == 30[178] && $location == "https://$name/"* ]] ||
      { echo "http://$name/ does not redirect to HTTPS (HTTP $code, Location $location)"; failed=1; }
  done
  return $failed
}

# plane_shell: runs Python from stdin in Django's shell inside Plane's API pod. The checks use
# it for what Plane's public API cannot do: creating API keys and webhooks. Only lines
# starting with "SMOKE " are its answer; the rest is Plane's logging.
plane_shell() {
  kubectl exec -i -n plane deploy/plane-api-wl -- python -W ignore manage.py shell --no-imports 2>/dev/null |
    sed -n 's/^SMOKE //p'
}

# with_plane_key <command...>: runs the command with PLANE_KEY set to a temporary API key of
# the instance admin in the workspace "smoke" (created on the first run), and deletes the key
# afterwards. The key also expires on its own after 10 minutes.
with_plane_key() {
  local rc
  PLANE_KEY=$(plane_shell <<'EOF'
from datetime import timedelta
from django.utils import timezone
from plane.db.models import APIToken, Workspace, WorkspaceMember
from plane.license.models import InstanceAdmin
admin = InstanceAdmin.objects.order_by("created_at").first().user
workspace, _ = Workspace.objects.get_or_create(slug="smoke", defaults={"name": "Smoke", "owner": admin})
WorkspaceMember.objects.get_or_create(workspace=workspace, member=admin, defaults={"role": 20})
APIToken.all_objects.filter(label="smoke-check").delete()
token = APIToken.objects.create(user=admin, workspace=workspace, label="smoke-check",
    description="platform/smoke.sh, deleted when the check ends",
    expired_at=timezone.now() + timedelta(minutes=10))
print("SMOKE", token.token)
EOF
  )
  [[ -n $PLANE_KEY ]] || { echo "could not create a temporary Plane API key"; return 1; }
  "$@"
  rc=$?
  plane_shell <<<'from plane.db.models import APIToken; APIToken.all_objects.filter(label="smoke-check").delete()' >/dev/null
  unset PLANE_KEY
  return $rc
}

# plane_api <method> <path> [json]: a call to Plane's public API in the workspace "smoke",
# through the ingress. The key goes to curl as a header file on a pipe, never as an argument.
plane_api() {
  curl -fsS --max-time 20 -X "$1" -H @<(printf 'X-API-Key: %s\n' "$PLANE_KEY") \
    -H 'Content-Type: application/json' ${3:+--data "$3"} \
    "https://plane.sdlc.internal/api/v1/workspaces/smoke$2"
}

# plane_probe: "<project id> <work item id>" of the work item "smoke probe" in the project
# SMOKE, both created on the first run.
plane_probe() {
  local project item
  project=$(plane_api GET /projects/ | jq -r '.results[] | select(.identifier == "SMOKE") | .id') || return 1
  [[ -n $project ]] ||
    project=$(plane_api POST /projects/ '{"name": "Smoke", "identifier": "SMOKE"}' | jq -er .id) || return 1
  item=$(plane_api GET "/projects/$project/issues/" | jq -r '.results[] | select(.name == "smoke probe") | .id' | head -1) ||
    return 1
  [[ -n $item ]] || item=$(plane_api POST "/projects/$project/issues/" '{"name": "smoke probe"}' | jq -er .id) || return 1
  echo "$project $item"
}

# toggle_probe_status <project> <item>: moves the probe between Todo and Done, checks that the
# change reads back, and prints the new state's id.
toggle_probe_status() {
  local states current target
  states=$(plane_api GET "/projects/$1/states/") || return 1
  current=$(plane_api GET "/projects/$1/issues/$2/" | jq -er .state) || return 1
  target=$(jq -r --arg current "$current" '
    (.results[] | select(.id == $current) | .group) as $group
    | [.results[] | select(.group == (if $group == "completed" then "unstarted" else "completed" end))][0].id // empty' \
    <<<"$states")
  [[ -n $target ]] || { echo "the project SMOKE has no Todo or Done state"; return 1; }
  plane_api PATCH "/projects/$1/issues/$2/" "$(jq -n --arg state "$target" '{state: $state}')" >/dev/null || return 1
  [[ $(plane_api GET "/projects/$1/issues/$2/" | jq -r .state) == "$target" ]] ||
    { echo "the status of smoke probe did not change"; return 1; }
  echo "$target"
}

plane_status_changes() {
  local probe
  probe=$(plane_probe) || return 1
  # shellcheck disable=SC2086 # two words: project and item
  toggle_probe_status $probe >/dev/null
}

# An automated client reads a work item with an API key and changes its status; the change
# shows in the UI (Smoke workspace, project SMOKE).
register plane_api_work_item
check_plane_api_work_item() { with_plane_key plane_status_changes; }

# A temporary echo endpoint in the cluster, addressed by its Service name, gets the webhook
# for a status change. It runs Python from Plane's own image, which the node already has.
PLANE_ECHO=smoke-plane-echo
plane_webhook_delivered() {
  local image probe state rc=1
  image=$(kubectl get deploy plane-api-wl -n plane -o jsonpath='{.spec.template.spec.containers[0].image}') || return 1
  probe=$(plane_probe) || return 1
  kubectl delete pod,service "$PLANE_ECHO" -n smoke --grace-period=1 --ignore-not-found >/dev/null
  kubectl run "$PLANE_ECHO" -n smoke --restart=Never --image="$image" --image-pull-policy=IfNotPresent \
    --port=8080 --expose --command -- python -u -c '
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state = body.get("data", {}).get("state")
        print("WEBHOOK", body.get("event"), body.get("action"), state.get("id") if isinstance(state, dict) else state)
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass
HTTPServer(("", 8080), Echo).serve_forever()' >/dev/null || return 1
  if kubectl wait pod/"$PLANE_ECHO" -n smoke --for=condition=Ready --timeout=60s >/dev/null &&
    [[ $(plane_shell <<EOF
from plane.app.serializers import WebhookSerializer
from plane.db.models import Workspace, Webhook
workspace = Workspace.objects.get(slug="smoke")
Webhook.all_objects.filter(workspace=workspace).delete()
webhook = WebhookSerializer(data={"url": "http://$PLANE_ECHO.smoke.svc:8080/", "issue": True}, context={"request": None})
print("SMOKE", webhook.save(workspace_id=workspace.id).id if webhook.is_valid() else webhook.errors)
EOF
    ) =~ ^[0-9a-f-]{36}$ ]]; then
    # shellcheck disable=SC2086 # two words: project and item
    if state=$(toggle_probe_status $probe); then
      for _ in $(seq 60); do
        kubectl logs "$PLANE_ECHO" -n smoke | grep -q "^WEBHOOK issue updated $state$" && { rc=0; break; }
        sleep 1
      done
      ((rc == 0)) || echo "no webhook for the status change reached $PLANE_ECHO.smoke.svc within 1 minute"
    else
      echo "$state"
    fi
  else
    echo "could not start $PLANE_ECHO or create its webhook"
  fi
  plane_shell <<<'from plane.db.models import Webhook; Webhook.all_objects.filter(workspace__slug="smoke").delete()' >/dev/null
  kubectl delete pod,service "$PLANE_ECHO" -n smoke --grace-period=1 --wait=false >/dev/null
  return $rc
}

register plane_webhook_in_cluster
check_plane_webhook_in_cluster() { with_plane_key plane_webhook_delivered; }

# The public mirror holds main and nothing else: no other branch, no tag. Read anonymously over
# HTTPS from the github remote's address; passes when there is no github remote.
register mirror_only_main
check_mirror_only_main() {
  local url refs
  url=$(git remote get-url github 2>/dev/null) || return 0
  url=$(sed -E 's#^git@github\.com:#https://github.com/#' <<<"$url")
  refs=$(git ls-remote "$url" 'refs/heads/*' 'refs/tags/*' | awk '{ print $2 }') || return 1
  [[ $refs == refs/heads/main ]] || { echo "the mirror $url holds more than main:"; echo "$refs"; return 1; }
}

register secrets_example_current
check_secrets_example_current() {
  diff <(platform/bootstrap.sh --example) platform/secrets.env.example
}

main() {
  local names=("$@") name out
  [[ ${#names[@]} -gt 0 ]] || names=("${CHECKS[@]}")
  if [[ ${#names[@]} -eq 0 ]]; then
    echo "no checks registered"
    return 0
  fi
  for name in "${names[@]}"; do
    if ! declare -F "check_$name" >/dev/null; then
      echo "unknown check: $name" >&2
      exit 2
    fi
    printf '%-40s ' "$name"
    if out=$("check_$name" 2>&1); then
      echo ok
    else
      echo FAIL
      [[ -z $out ]] || sed 's/^/  /' <<<"$out"
      exit 1
    fi
  done
}

# Sourcing the script (for tests) defines the checks without running them.
[[ ${BASH_SOURCE[0]} != "$0" ]] || main "$@"
