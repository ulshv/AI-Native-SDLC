#!/usr/bin/env bash
# Checks for secrets before they reach the public GitHub mirror: exact values from
# secrets.env, plus well-known secret formats with gitleaks when it is installed.
#
#   platform/secrets-leak-check.sh            the whole history of HEAD and the working tree,
#                                             uncommitted and untracked files included
#   platform/secrets-leak-check.sh <range>    only the commits in a revision range
#
# The pre-push hook (platform/hooks/pre-push) runs it with the pushed range on every push to
# GitHub. Exits 1 on a finding, naming the commit or file, never the value.
set -euo pipefail

SECRETS_FILE=${SECRETS_DIR:-$HOME/.config/ai-native-sdlc}/secrets.env

refuse() {
  printf 'secrets-leak-check: %s\n' "$*" >&2
  exit 1
}

# Every non-empty value in secrets.env, one per line. Never print it.
secret_values() { sed -n 's/^[A-Za-z0-9_]*=//p' "$SECRETS_FILE" | grep -v '^$'; }

range=${1:-HEAD}
worktree=$((! $#))

[[ -s $SECRETS_FILE ]] || refuse "no $SECRETS_FILE to check against"

for commit in $(git rev-list "$range"); do
  files=$(git grep -lF -f <(secret_values) "$commit" -- 2>/dev/null | sed "s/^$commit://" | paste -sd' ' || true)
  [[ -z $files ]] || refuse "commit ${commit:0:7} contains a value from secrets.env in: $files"
done

if ((worktree)); then
  files=$(git grep -lF --untracked -f <(secret_values) -- 2>/dev/null | paste -sd' ' || true)
  [[ -z $files ]] || refuse "the working tree contains a value from secrets.env in: $files"
fi

if command -v gitleaks >/dev/null; then
  gitleaks git --no-banner --redact --log-opts="$range" . >&2 || refuse "gitleaks found a secret in $range"
  if ((worktree)); then
    gitleaks dir --no-banner --redact . >&2 || refuse "gitleaks found a secret in the working tree"
  fi
else
  echo "secrets-leak-check: gitleaks is not installed; checked exact values from secrets.env only" >&2
fi

scope="$(git rev-list --count "$range") commits in $range"
((worktree)) && scope+=" and the working tree"
echo "secrets-leak-check: $scope, no secrets found" >&2
