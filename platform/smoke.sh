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

# Processes in one container can signal and inspect each other under the default AppArmor
# profile. Fails when the host's kernel stacks container profiles and the profile from
# platform/apparmor/ is not loaded (containerd#14485): Postgres and Go programs then degrade.
register container_signals
check_container_signals() {
  run_pod default smoke-container-signals \
    --image=docker.io/rancher/mirrored-library-busybox:1.37.0 --image-pull-policy=IfNotPresent -- \
    sh -c 'sleep 30 & p=$!
      kill -URG $p || { echo "a container process cannot signal another one (AppArmor)"; exit 1; }
      cat /proc/$p/environ >/dev/null || { echo "a container process cannot read another one (AppArmor)"; exit 1; }
      kill $p'
}

# Runs psql in a throwaway pod with each checked role's password from its Secret, so no
# password appears on a command line. Each role opens its own database and is refused another.
register database_isolation
check_database_isolation() {
  local image pod=smoke-database-isolation
  image=$(kubectl get cluster platform-pg -n databases -o jsonpath='{.spec.imageName}') || return 1
  run_pod databases "$pod" --image="$image" --overrides="$(
    jq -n --arg pod "$pod" --arg image "$image" '{spec: {containers: [{
      name: $pod, image: $image,
      env: [
        {name: "PW_gitea", valueFrom: {secretKeyRef: {name: "pg-gitea", key: "password"}}},
        {name: "PW_orchestrator", valueFrom: {secretKeyRef: {name: "pg-orchestrator", key: "password"}}}
      ],
      command: ["sh", "-c", "
        for pair in gitea:plane orchestrator:gitea; do
          role=${pair%:*} other=${pair#*:}
          eval \"export PGPASSWORD=\\$PW_$role\"
          psql \"host=platform-pg-rw user=$role dbname=$role\" -tAc \"select 1\" >/dev/null ||
            { echo \"$role cannot open its own database\"; exit 1; }
          if psql \"host=platform-pg-rw user=$role dbname=$other\" -tAc \"select 1\" 2>/tmp/err; then
            echo \"$role opened the $other database\"; exit 1
          fi
          grep -q \"pg_hba.conf rejects connection\" /tmp/err || { cat /tmp/err; exit 1; }
        done"]
    }]}}'
  )"
}

# with_service <namespace> <service> <port> <command...>: runs the command with SERVICE_URL
# pointing at a temporary port-forward to the Service, so it needs no ingress.
with_service() {
  local namespace=$1 service=$2 service_port=$3 log pid port rc
  shift 3
  log=$(mktemp)
  kubectl port-forward -n "$namespace" "svc/$service" "0:$service_port" >"$log" 2>&1 &
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
  SERVICE_URL=http://127.0.0.1:$port "$@"
  rc=$?
  kill "$pid" 2>/dev/null
  rm -f "$log"
  return $rc
}

# with_gitea <command...>: the command with GITEA_URL pointing at Gitea's Service, so it works
# before the ingress exists.
with_gitea() { with_service gitea gitea-http 3000 as_gitea "$@"; }
as_gitea() { GITEA_URL=$SERVICE_URL "$@"; }

# The admin signs in through the API with the password from secrets.env. The password goes
# to curl through a netrc on a pipe, never as an argument.
gitea_admin_is_admin() {
  local password
  password=$(sed -n 's/^GITEA_ADMIN_PASSWORD=//p' "$SECRETS_FILE")
  curl -fsS --max-time 10 --netrc-file <(printf 'machine 127.0.0.1 login gitea_admin password %s\n' "$password") \
    "$GITEA_URL/api/v1/user" | jq -e '.login == "gitea_admin" and .is_admin' >/dev/null ||
    { echo "gitea_admin cannot sign in as an admin"; return 1; }
}

# gitea_admin_get <path> (inside with_gitea): a GET on Gitea's API as the admin.
gitea_admin_get() {
  local password
  password=$(sed -n 's/^GITEA_ADMIN_PASSWORD=//p' "$SECRETS_FILE")
  curl -fsS --max-time 10 --netrc-file <(printf 'machine 127.0.0.1 login gitea_admin password %s\n' "$password") \
    "$GITEA_URL/api/v1$1"
}

# newest_image <org> (inside with_gitea): the newest tagged container image of an organization
# in Gitea's registry, empty when it has none.
newest_image() {
  gitea_admin_get "/packages/$1?type=container&limit=50" |
    jq -r --arg org "$1" '[.[] | select(.version | startswith("sha256:") | not)] | sort_by(.created_at) | last
      | if . then "gitea.sdlc.internal/\($org)/\(.name):\(.version)" else empty end'
}

newest_images() { newest_image platform; newest_image sdlc; }

# pulls <image>: a pod in namespace default pulls the image without a pull secret. The command
# does not exist, so nothing of the image runs; imagePullPolicy Always makes the node ask the
# registry even when it has the image.
pulls() {
  local pod=smoke-registry-pull state="" uid
  kubectl delete pod "$pod" -n default --ignore-not-found --wait=true >/dev/null
  kubectl run "$pod" -n default --restart=Never --image="$1" --image-pull-policy=Always \
    --command -- /smoke-registry-pull-runs-nothing >/dev/null || return 1
  # Events outlive their pod, so only this pod's (by uid) count.
  uid=$(kubectl get pod "$pod" -n default -o jsonpath='{.metadata.uid}') || return 1
  for _ in $(seq 60); do
    if [[ -n $(kubectl get events -n default -o name \
      --field-selector "involvedObject.uid=$uid,reason=Pulled" 2>/dev/null) ]]; then
      state=pulled
      break
    fi
    state=$(kubectl get pod "$pod" -n default -o jsonpath='{.status.containerStatuses[0].state.waiting.reason}' 2>/dev/null)
    [[ $state == ErrImagePull || $state == ImagePullBackOff ]] && break
    sleep 2
  done
  kubectl delete pod "$pod" -n default --wait=true >/dev/null
  [[ $state == pulled ]] || { echo "$1: ${state:-not pulled in 2 minutes}"; return 1; }
}

# k3s pulls from both organizations of Gitea's registry as reader (registries.yaml): the newest
# image of each is pulled. An organization without images yet is only reported.
register registry_pull
check_registry_pull() {
  local images image org
  images=$(with_gitea newest_images) || return 1
  for org in platform sdlc; do
    image=$(grep "^gitea.sdlc.internal/$org/" <<<"$images" || true)
    if [[ -z $image ]]; then echo "  (no image in $org yet: nothing to pull)"; continue; fi
    pulls "$image" || return 1
  done
}

# Both Actions runners are online, each where it belongs: ci-platform on this repository only,
# ci-products on the sdlc organization. Ephemeral runners re-register after every job, so the
# check looks for an online runner whose name starts with the runner's.
ci_runners_online() {
  gitea_admin_get /repos/platform/ai-native-sdlc/actions/runners |
    jq -e '[.runners[]? | select(.status == "online" and (.name | startswith("ci-platform")))] | length > 0' >/dev/null ||
    { echo "ci-platform is not online on platform/ai-native-sdlc"; return 1; }
  gitea_admin_get /orgs/sdlc/actions/runners |
    jq -e '[.runners[]? | select(.status == "online" and (.name | startswith("ci-products")))] | length > 0' >/dev/null ||
    { echo "ci-products is not online on the sdlc organization"; return 1; }
  ! gitea_admin_get /orgs/sdlc/actions/runners | jq -e 'any(.runners[]?; .name | startswith("ci-platform"))' >/dev/null ||
    { echo "ci-platform is registered on the whole organization"; return 1; }
}

register ci_runners
check_ci_runners() { with_gitea ci_runners_online; }

# CI runs code an agent wrote: no pod in ci is privileged, mounts a host path (a container
# runtime socket among them) or shares the host's user namespace.
register ci_unprivileged
check_ci_unprivileged() {
  local bad
  bad=$(kubectl get pods -n ci -o json | jq -r '.items[] | select(
      .spec.hostUsers != false
      or any(.spec.volumes[]?; .hostPath)
      or any((.spec.initContainers // []) + .spec.containers | .[]; .securityContext.privileged == true)
    ) | .metadata.name')
  [[ -z $bad ]] || { echo "pods in ci that are privileged, mount a host path or share the host's users:"; echo "$bad"; return 1; }
  [[ -n $(kubectl get pods -n ci -o name) ]] || { echo "no pods in ci"; return 1; }
}

# Agent runs (namespace sdlc-agents) reach Gitea and the model provider, and nothing private: not
# the database, Plane, Traefik (and the web UIs behind it) or the Kubernetes API (design decision
# 7). A pod there tries each with a TCP connect.
register agents_network
check_agents_network() {
  local provider
  provider=$(kubectl get configmap -n sdlc-agents opencode-config -o jsonpath='{.data.opencode\.json}' |
    jq -r '.provider.llm.options.baseURL' | sed -E 's#^https?://([^/:]+).*#\1#') || return 1
  run_pod sdlc-agents smoke-agents-network \
    --image=docker.io/rancher/mirrored-library-busybox:1.37.0 --image-pull-policy=IfNotPresent -- \
    sh -c "fail=0
      for ok in gitea-http.gitea.svc:3000 $provider:443; do
        nc -z -w 5 \${ok%:*} \${ok#*:} || { echo \"cannot reach \$ok\"; fail=1; }
      done
      for no in platform-pg-rw.databases.svc:5432 plane-api.plane.svc:8000 traefik.traefik.svc:443 kubernetes.default.svc:443; do
        ! nc -z -w 3 \${no%:*} \${no#*:} || { echo \"reached \$no, which agent runs must not\"; fail=1; }
      done
      exit \$fail"
}

register gitea_admin_login
check_gitea_admin_login() { with_gitea gitea_admin_is_admin; }

# gitea_main_head: the commit main points to on Gitea (runs inside with_gitea).
gitea_main_head() {
  local password
  password=$(sed -n 's/^GITEA_ADMIN_PASSWORD=//p' "$SECRETS_FILE")
  curl -fsS --max-time 10 --netrc-file <(printf 'machine 127.0.0.1 login gitea_admin password %s\n' "$password") \
    "$GITEA_URL/api/v1/repos/platform/ai-native-sdlc/branches/main" | jq -er .commit.id
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

# Every target project's file has the format of platform/projects/project.schema.json, which the
# orchestrator reads them with.
register project_files_valid
check_project_files_valid() {
  python3 - platform/projects/project.schema.json platform/projects/*/project.yaml <<'EOF'
import json, sys
try:
    import jsonschema, yaml
except ImportError as error:
    sys.exit(f"python3 lacks {error.name} (apt install python3-jsonschema python3-yaml)")
schema = json.load(open(sys.argv[1]))
validator = jsonschema.Draft202012Validator(schema)
bad = []
for path in sys.argv[2:]:
    for error in validator.iter_errors(yaml.safe_load(open(path))):
        bad.append(f"{path}: {'/'.join(map(str, error.path)) or '(top)'}: {error.message}")
if bad:
    sys.exit("\n".join(bad))
EOF
}

# Every target project is seeded and its settings hold (platform/seed-projects.sh): its repository
# exists, agent and agent-runner may write to it, and branch protection matches design decision 8.
# A dry run of the seeding reports anything that would change. Spike 1.4 showed Gitea enforcing
# exactly these settings, so the check compares them instead of merging for real.
register target_projects_seeded
check_target_projects_seeded() {
  local out
  out=$(platform/seed-projects.sh --dry-run 2>&1) || { echo "$out"; return 1; }
  ! grep -qE '^\s+(todo|FAIL)' <<<"$out" || { grep -E '^\s+(todo|FAIL)' <<<"$out"; return 1; }
}

# Plane is set up for the loop (apps/plane/setup.py): for every target project, its Plane project
# has every status of the loop, and agent and the file's people with a Plane account as members;
# its workspace has an active webhook to the orchestrator for work items and comments, signed
# with PLANE_WEBHOOK_SECRET; and agent's API key in sdlc/plane-agent works. The statuses are read
# from setup.py, so the two cannot drift apart.
loop_states() {
  python3 -c 'import ast, json, sys
tree = ast.parse(open(sys.argv[1]).read())
node = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "LOOP_STATES")
print(json.dumps([s[0] for s in ast.literal_eval(node.value)]))' platform/apps/plane/setup.py
}

register plane_loop_project
check_plane_loop_project() {
  local file workspace identifier people states problems key
  states=$(loop_states) || return 1
  for file in platform/projects/*/project.yaml; do
    workspace=$(yq -r .tracker.workspace <"$file")
    identifier=$(yq -r .tracker.identifier <"$file")
    people=$(yq -o json -I0 '[.people[] | select(.plane) | .plane]' <"$file")
    # The webhook secret travels on the shell's stdin, never as an argument.
    problems=$(plane_shell <<EOF
from plane.db.models import Project, ProjectMember, State, Webhook
problems = []
project = Project.objects.filter(workspace__slug="$workspace", identifier="$identifier").first()
if project is None:
    problems.append("no project $identifier in workspace $workspace")
else:
    have = set(State.objects.filter(project=project).values_list("name", flat=True))
    missing = [s for s in $states if s not in have]
    if missing:
        problems.append("$identifier lacks the statuses " + ", ".join(missing))
    members = set(ProjectMember.objects.filter(project=project, is_active=True).values_list("member__username", flat=True))
    absent = [u for u in ["agent"] + $people if u not in members]
    if absent:
        problems.append("not members of $identifier: " + ", ".join(absent))
if not Webhook.objects.filter(workspace__slug="$workspace", url="http://orchestrator.sdlc.svc/hooks/plane",
                              is_active=True, issue=True, issue_comment=True,
                              secret_key="$(sed -n 's/^PLANE_WEBHOOK_SECRET=//p' "$SECRETS_FILE")").exists():
    problems.append("workspace $workspace has no active webhook to the orchestrator signed with PLANE_WEBHOOK_SECRET")
print("SMOKE", "; ".join(problems) or "ok")
EOF
    )
    [[ $problems == ok ]] || { echo "${problems:-no answer from the Plane shell}"; return 1; }
  done
  key=$(kubectl get secret -n sdlc plane-agent -o jsonpath='{.data.api-key}' | base64 -d)
  curl -fsS --max-time 20 -o /dev/null -H @<(printf 'X-API-Key: %s\n' "$key") \
    "https://plane.sdlc.internal/api/v1/workspaces/$workspace/projects/" ||
    { echo "agent's API key in sdlc/plane-agent does not work"; return 1; }
}

# The orchestrator runs (platform/apps/orchestrator): its Deployment is rolled out with the image
# of a commit on main, it answers /healthz (which reads its database) through its Service, and
# the webhooks of both tools point at it: the sdlc organization's in Gitea (registered by the
# bootstrap) and each target project's workspace in Plane (check plane_loop_project).
orchestrator_healthy() {
  curl -fsS --max-time 10 "$SERVICE_URL/healthz" | jq -e '.ok' >/dev/null ||
    { echo "the orchestrator does not answer /healthz"; return 1; }
}

org_webhook_to_orchestrator() {
  gitea_admin_get /orgs/sdlc/hooks | jq -e '
    any(.[]; .config.url == "http://orchestrator.sdlc.svc/hooks/gitea" and .active and
      ((["pull_request", "pull_request_review", "issue_comment", "release"] - .events) | length) == 0)' >/dev/null ||
    { echo "the sdlc organization has no active webhook to the orchestrator (run platform/bootstrap.sh)"; return 1; }
}

register orchestrator_running
check_orchestrator_running() {
  local tag
  kubectl rollout status deploy/orchestrator -n sdlc --timeout=30s >/dev/null ||
    { echo "the orchestrator's Deployment in sdlc is not rolled out"; return 1; }
  tag=$(kubectl get deploy orchestrator -n sdlc -o jsonpath='{.spec.template.spec.containers[0].image}')
  tag=${tag##*:}
  git merge-base --is-ancestor "$tag" main 2>/dev/null ||
    { echo "the orchestrator runs $tag, which is no commit on main"; return 1; }
  with_service sdlc orchestrator 80 orchestrator_healthy || return 1
  with_gitea org_webhook_to_orchestrator
}

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
