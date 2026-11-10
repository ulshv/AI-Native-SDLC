#!/usr/bin/env bash
# Connects every target project in platform/projects/ to the platform's Gitea (design decision 9).
#   platform/seed-projects.sh            create missing repositories from their local clones, and
#                                        converge access and branch protection; safe to re-run
#   platform/seed-projects.sh --dry-run  print the plan and change nothing
#
# Run it after platform/bootstrap.sh. It needs no sudo, only Gitea's API. A repository that
# exists already keeps its contents: the clone is pushed only to create it.
set -euo pipefail
# shellcheck source=bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/bootstrap.sh"
DRY_RUN=false

# --- Reading the project files ------------------------------------------------------------------

PROJECT_FILES=(platform/projects/*/project.yaml)

field() { yq -r "$2" <"$1"; } # field <project file> <yq expression>

# clone_of <project file>: the local clone, with ~ expanded; <NAME>_CLONE overrides the file.
clone_of() {
  local name var path
  name=$(field "$1" .name)
  var=$(tr 'a-z-' 'A-Z_' <<<"$name")_CLONE
  path=${!var:-$(field "$1" .repository.clone)}
  printf '%s' "${path/#\~/$HOME}"
}

# gitea_users <project file> <role...>: the Gitea usernames of the people with any of the roles.
gitea_users() {
  local file=$1
  shift
  yq -o json -I0 '[.people[] | select(.gitea)]' <"$file" |
    jq -c --args '[.[] | select(any(.roles[]; IN($ARGS.positional[]))) | .gitea]' "$@"
}

# --- Checks before any change ------------------------------------------------------------------

# Every clone exists and has both long-lived branches; all problems are listed at once.
clones_ready() {
  local file clone branch problems=()
  for file in "${PROJECT_FILES[@]}"; do
    clone=$(clone_of "$file")
    if ! git -C "$clone" rev-parse --git-dir >/dev/null 2>&1; then
      problems+=("$(field "$file" .name): no git clone at $clone")
      continue
    fi
    for branch in $(field "$file" '.repository.base, .repository.release'); do
      git -C "$clone" show-ref --verify --quiet "refs/heads/$branch" ||
        problems+=("$(field "$file" .name): $clone has no branch $branch")
    done
  done
  ((${#problems[@]} == 0)) || { printf '  FAIL  %s\n' "${problems[@]}" >&2; return 1; }
}

# --- One project ------------------------------------------------------------------------------------

project_repo_exists() { [[ -n ${GITEA_URL-} ]] && gitea_api GET "/repos/$REPO" >/dev/null 2>&1; }

# Creates the repository and pushes the clone into it: the release branch first, because Gitea
# makes the first branch pushed to an empty repository its default, then every branch and tag.
create_project_repo() {
  local push
  gitea_api POST /orgs/sdlc/repos "$(jq -nc --arg name "${REPO#*/}" '{name: $name, private: true}')" >/dev/null
  push=(git -C "$CLONE" -c credential.helper= -c "credential.helper=$(credential_helper)" push "$GITEA_URL/$REPO.git")
  "${push[@]}" "refs/heads/$RELEASE:refs/heads/$RELEASE"
  "${push[@]}" 'refs/heads/*:refs/heads/*' 'refs/tags/*:refs/tags/*'
}

# Pull requests target the base branch unless someone picks another one.
default_branch_current() {
  [[ $(gitea_api GET "/repos/$REPO" 2>/dev/null | jq -r .default_branch) == "$BASE" ]]
}
set_default_branch_to_base() { gitea_api PATCH "/repos/$REPO" "$(jq -nc --arg b "$BASE" '{default_branch: $b}')" >/dev/null; }

agents_can_write() {
  local user
  for user in agent agent-runner; do
    [[ $(gitea_api GET "/repos/$REPO/collaborators/$user/permission" 2>/dev/null | jq -r .permission) == write ]] || return 1
  done
}

grant_agents_write() {
  local user
  for user in agent agent-runner; do
    gitea_api PUT "/repos/$REPO/collaborators/$user" '{"permission": "write"}' >/dev/null
  done
}

# Branch protection (design decision 8). Nobody pushes to either branch directly. development
# merges by its engineers and by agent, the orchestrator, without a required review: the gate
# there is a person's request, checked by the orchestrator's policy. main merges only by
# engineers, after an approval by one of the project's people (the approvals allowlist keeps an
# approval by agent-runner, which has write access, from counting). agent-runner is on no list.
protection() { # protection <branch>
  local engineers people
  engineers=$(gitea_users "$FILE" engineer)
  people=$(gitea_users "$FILE" product_owner engineer)
  if [[ $1 == "$BASE" ]]; then
    jq -nc --arg b "$1" --argjson m "$engineers" '{rule_name: $b, enable_push: false,
      enable_merge_whitelist: true, merge_whitelist_usernames: ($m + ["agent"] | unique),
      required_approvals: 0}'
  else
    jq -nc --arg b "$1" --argjson m "$engineers" --argjson a "$people" '{rule_name: $b, enable_push: false,
      enable_merge_whitelist: true, merge_whitelist_usernames: ($m | unique),
      required_approvals: 1, enable_approvals_whitelist: true, approvals_whitelist_username: ($a | unique),
      block_on_rejected_reviews: true, dismiss_stale_approvals: true}'
  fi
}

# The live rule agrees with every field of the wanted one; Gitea answers with more fields.
protection_current() { # <branch>
  local want live
  want=$(protection "$1")
  live=$(gitea_api GET "/repos/$REPO/branch_protections/$1" 2>/dev/null) || return 1
  jq -e --argjson want "$want" '. as $live | all($want | to_entries[];
    (.value | if type == "array" then sort else . end) == ($live[.key] | if type == "array" then sort else . end))' \
    <<<"$live" >/dev/null
}

set_protection() { # <branch>
  if gitea_api GET "/repos/$REPO/branch_protections/$1" >/dev/null 2>&1; then
    gitea_api PATCH "/repos/$REPO/branch_protections/$1" "$(protection "$1" | jq -c 'del(.rule_name)')" >/dev/null
  else
    gitea_api POST "/repos/$REPO/branch_protections" "$(protection "$1")" >/dev/null
  fi
}

protections_current() { protection_current "$BASE" && protection_current "$RELEASE"; }
set_protections() { set_protection "$BASE" && set_protection "$RELEASE"; }

# The clone gets Gitea as origin when it has none, with this clone's credential helper.
clone_origin_set() { git -C "$CLONE" remote get-url origin >/dev/null 2>&1; }
set_clone_origin() {
  git -C "$CLONE" remote add origin "$GITEA_HOST/$REPO.git"
  git -C "$CLONE" config --unset-all "credential.$GITEA_HOST.helper" || true
  git -C "$CLONE" config --add "credential.$GITEA_HOST.helper" ""
  git -C "$CLONE" config --add "credential.$GITEA_HOST.helper" "$(credential_helper)"
}

seed_project() {
  FILE=$1
  REPO=$(field "$FILE" .repository.gitea)
  BASE=$(field "$FILE" .repository.base)
  RELEASE=$(field "$FILE" .repository.release)
  CLONE=$(clone_of "$FILE")
  log "-- $(field "$FILE" .name) ($REPO from $CLONE)"
  step "repository $REPO exists (created from the clone when missing)" project_repo_exists create_project_repo
  step "its default branch is $BASE" default_branch_current set_default_branch_to_base
  step "agent and agent-runner may write to it" agents_can_write grant_agents_write
  step "$BASE and $RELEASE are protected (design decision 8)" protections_current set_protections
  step "the clone's origin is set (Gitea when it had none)" clone_origin_set set_clone_origin
}

seed_main() {
  case ${1-} in
    "") ;;
    --dry-run) DRY_RUN=true ;;
    -h | --help) sed -n '2,8s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; return 0 ;;
    *) sed -n '2,8s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2; return 2 ;;
  esac
  log "== checks"
  clones_ready || { echo "error: nothing was changed" >&2; return 1; }
  log "  ok    every clone is there with its branches"
  [[ -s $PUSH_TOKEN_FILE ]] || { echo "error: no Gitea push token; run platform/bootstrap.sh first" >&2; return 1; }
  start_gitea_forward
  local file
  for file in "${PROJECT_FILES[@]}"; do seed_project "$file"; done
  if $DRY_RUN; then log "dry run: nothing was changed"; else log "seeding complete"; fi
}

[[ ${BASH_SOURCE[0]} != "$0" ]] || seed_main "$@"
