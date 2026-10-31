#!/usr/bin/env bash
# The only imperative entry point of the platform. See platform/README.md.
#   platform/bootstrap.sh            build the platform, or converge it; safe to re-run
#   platform/bootstrap.sh --dry-run  print the plan and change nothing
set -euo pipefail
cd "$(dirname "$0")/.."

# Pinned k3s release. Chart versions are pinned in platform/apps/*/app.yaml.
K3S_VERSION=v1.36.5+k3s1

SECRETS_DIR=${SECRETS_DIR:-$HOME/.config/ai-native-sdlc}
SECRETS_FILE=$SECRETS_DIR/secrets.env
MANIFEST=platform/secrets.manifest
TOOLS=(helm git openssl jq yq curl certutil)
PORTS=(80 443)

DRY_RUN=false

log() { printf '%s\n' "$*"; }

# step <description> <check> <action>: runs <action> unless <check> reports it is already done.
# Every change to the host or the cluster goes through a step, which is what makes re-runs
# safe and --dry-run honest.
step() {
  local desc=$1 check=$2 action=$3
  if $check; then
    log "  done  $desc"
  elif $DRY_RUN; then
    log "  todo  $desc"
  else
    log "  run   $desc"
    $action
  fi
}

# secret_value <name>: the value of <name> in secrets.env, empty when absent.
secret_value() {
  [[ -f $SECRETS_FILE ]] || return 0
  sed -n "s/^$1=//p" "$SECRETS_FILE" | tail -n 1
}

# Names of external secrets that must be present (manifest columns: name kind required ...).
required_external_secrets() {
  awk '!/^#/ && NF && $2 == "external" && $3 == "yes" { print $1 }' "$MANIFEST"
}

# port_holder <port>: names of the processes listening on <port>, empty when it is free.
port_holder() {
  local out
  out=$(sudo -n ss -Hltnp "sport = :$1" 2>/dev/null || ss -Hltnp "sport = :$1")
  [[ -n $out ]] || return 0
  grep -o '"[^"]*"' <<<"$out" | tr -d '"' | sort -u | paste -sd, - | grep . || echo "an unknown process"
}

sudo_available() {
  if $DRY_RUN; then
    # A dry run must not prompt for a password, so it settles for group membership.
    sudo -n true 2>/dev/null || id -nG | grep -qwE 'sudo|admin|wheel'
  else
    sudo -v
  fi
}

# Checks every prerequisite and changes nothing. Reports all problems at once.
phase_preflight() {
  local problems=() tool port holder name
  [[ $EUID -ne 0 ]] || problems+=("running as root: run as your own user, the script calls sudo itself")
  for tool in "${TOOLS[@]}"; do
    command -v "$tool" >/dev/null || problems+=("missing tool: $tool")
  done
  if command -v yq >/dev/null && ! yq --version 2>&1 | grep -q mikefarah; then
    problems+=("wrong yq: need mikefarah/yq v4, not the apt package")
  fi
  sudo_available || problems+=("sudo is not available for $USER")
  for port in "${PORTS[@]}"; do
    holder=$(port_holder "$port")
    [[ -z $holder ]] || problems+=("port $port is in use by $holder")
  done
  for name in $(required_external_secrets); do
    [[ -n $(secret_value "$name") ]] || problems+=("missing secret: $name (add it to $SECRETS_FILE)")
  done

  if [[ ${#problems[@]} -eq 0 ]]; then
    log "  ok    all prerequisites present"
    return 0
  fi
  printf '  FAIL  %s\n' "${problems[@]}" >&2
  $DRY_RUN || exit 1
  PREFLIGHT_FAILED=true
}

# Generates missing secret values and the root CA into the secrets directory.
phase_secrets() { :; }

# Installs k3s, the /etc/hosts block and host CA trust, and applies Kubernetes Secrets.
phase_host() { :; }

# Installs the CloudNativePG operator, the Postgres cluster and Gitea with Helm.
phase_git_hosting() { :; }

# Creates the Gitea org and repo, moves origin to Gitea and pushes branches and tags.
phase_repository() { :; }

# Installs Argo CD with Helm and applies the root Application.
phase_gitops() { :; }

# Waits until Argo CD has everything Synced/Healthy, then drops the Helm release records.
phase_adoption() { :; }

PHASES=(preflight secrets host git_hosting repository gitops adoption)

usage() { sed -n '2,4s/^# \{0,1\}//p' "$0"; }

main() {
  case ${1-} in
    "") ;;
    --dry-run) DRY_RUN=true ;;
    -h | --help) usage; return 0 ;;
    *) usage >&2; return 2 ;;
  esac

  PREFLIGHT_FAILED=false
  local i
  for i in "${!PHASES[@]}"; do
    log "== $i ${PHASES[$i]//_/ }"
    "phase_${PHASES[$i]}"
  done

  if $DRY_RUN; then
    log "dry run: nothing was changed"
    ! $PREFLIGHT_FAILED || return 1
  else
    log "bootstrap complete"
  fi
}

main "$@"
