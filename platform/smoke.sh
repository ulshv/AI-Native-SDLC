#!/usr/bin/env bash
# Platform smoke checks.
#   platform/smoke.sh          run every registered check
#   platform/smoke.sh <name>   run the named checks only
# Stops at the first failing check and exits non-zero.
set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS_FILE=${SECRETS_DIR:-$HOME/.config/ai-native-sdlc}/secrets.env

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

# A pod resolves a Service name through the cluster DNS Service. Fails when something on the
# host, such as a VPN in TUN mode, steers pods' DNS traffic away from CoreDNS.
register pod_dns
check_pod_dns() {
  local pod=smoke-pod-dns want
  want=$(kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}') || return 1
  kubectl delete pod "$pod" -n default --ignore-not-found >/dev/null
  kubectl run "$pod" -n default --rm -i --quiet --restart=Never \
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
  kubectl delete pod "$pod" -n databases --ignore-not-found >/dev/null
  kubectl run "$pod" -n databases --rm -i --quiet --restart=Never --image="$image" --overrides="$(
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

main "$@"
