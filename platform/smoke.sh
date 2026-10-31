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
