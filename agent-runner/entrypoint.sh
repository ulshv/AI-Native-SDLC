#!/usr/bin/env bash
# One agent run (design decision 7). Everything around the model is deterministic and lives here:
# clone, branch, the OpenCode command, the checks, commit, push and the result. The run reads
# its task from /task/task.json:
#
#   {"mode": "propose" | "apply" | "fix" | "archive",
#    "repo": "sdlc/example-webapp", "base": "development", "branch": "feat/EWA-12_slug",
#    "change": "ewa-12-slug", "model": "llm/deepseek/deepseek-v4-flash-0731",
#    "ticket": {"key": "EWA-12", "title": "...", "text": "..."},
#    "answers": [{"question": "...", "answer": "..."}],   (optional)
#    "feedback": "...",                                    (optional, for propose and fix)
#    "unarchive": true,                                    (optional, for fix: the change was archived)
#    "checks": ["npm ci", "npm test"]}                     (apply and fix)
#
# and ends with one JSON in /dev/termination-log, which the orchestrator reads from the pod's
# status: {"result": "spec_ready" | "code_ready" | "archived", "commit": "...", "branch": "..."},
# {"result": "questions", "questions": [...]} with nothing pushed, or {"result": "failed",
# "reason": "..."}.
#
# The run pushes to its own branch only, as agent-runner, which no branch protection lets merge.
# -E: the ERR trap below fires inside functions too, so every failure ends with a result.
set -Eeuo pipefail

TASK=${TASK:-/task/task.json}
TOKEN_FILE=${TOKEN_FILE:-/secrets/gitea/token}
GITEA=${GITEA:-http://gitea-http.gitea.svc:3000}
RESULT=${RESULT:-/dev/termination-log}
WORK=${WORK:-/work}

task() { jq -r "$1 // empty" "$TASK"; }

# redact <text>: the text without the model provider's key. A tool's error can quote its config
# with the key filled in (OpenCode does on a config it cannot parse), and whatever the run prints
# ends up in the pod's log and status.
redact() {
  local text=$1 key=${LLM_API_KEY-}
  if ((${#key} >= 8)); then
    text=${text//"$key"/<LLM_API_KEY>}
    key=${key//\"/}
    text=${text//"$key"/<LLM_API_KEY>}
  fi
  printf '%s' "$text"
}

# result <json>: the run's one answer. A termination message holds at most 4096 bytes and the
# kubelet cuts it there, which breaks the JSON: whoever builds a result keeps it under
# MAX_RESULT_BYTES, counting bytes, not characters (Cyrillic takes two, escapes up to six).
MAX_RESULT_BYTES=3900
fits() { (($(printf '%s' "$1" | wc -c) <= MAX_RESULT_BYTES)); }

# fitted <n> <jq arguments...>: the JSON jq builds with $n as the length of its long texts, $n
# halved until it fits.
fitted() {
  local n=$1 message
  shift
  while :; do
    message=$(jq -c --argjson n "$n" "$@")
    if fits "$message" || ((n == 0)); then break; fi
    n=$((n / 2))
  done
  printf '%s' "$message"
}

result() {
  local message
  message=$(redact "$1")
  printf '%s' "$message" >"$RESULT"
  printf 'agent-run: result %s\n' "$message"
}

fail() {
  trap - ERR
  result "$(fitted 1500 -n --arg reason "$1" --arg branch "${BRANCH-}" \
    '{result: "failed", reason: $reason[0:$n], branch: $branch}')"
  exit 1
}
trap 'fail "the run stopped at line $LINENO of agent-run"' ERR

log() { printf 'agent-run: %s\n' "$(redact "$*")"; }

MODE=$(task .mode)
REPO=$(task .repo)
BASE=$(task .base)
BRANCH=$(task .branch)
CHANGE=$(task .change)
MODEL=$(task .model)
KEY=$(task .ticket.key)

[[ $MODE =~ ^(propose|apply|fix|archive)$ ]] || fail "unknown mode: $MODE"
[[ $BRANCH == feat/* || $BRANCH == archive/* ]] || fail "refusing to push to $BRANCH: a run pushes to feat/ or archive/ branches only"
[[ $CHANGE =~ ^[a-z0-9][a-z0-9-]*$ ]] || fail "not a change name: $CHANGE"
log "$MODE $KEY on $REPO $BRANCH (change $CHANGE, model ${MODEL:-none}, image ${AGENT_RUNNER_COMMIT:-unknown})"

# --- The repository ------------------------------------------------------------------------------

# The token reaches git through a credential helper that reads the mounted file when git asks, so
# it is never on a command line or in a config file.
git config --global user.name agent-runner
git config --global user.email agent-runner@sdlc.internal
git config --global credential.helper \
  "!f() { test \"\$1\" = get || exit 0; echo username=agent-runner; echo \"password=\$(cat '$TOKEN_FILE')\"; }; f"

cd "$WORK"
git clone --quiet "$GITEA/$REPO.git" repo
cd repo
# The branch continues where the last run left it; a new one starts from the base branch.
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git checkout --quiet "$BRANCH"
else
  git checkout --quiet -b "$BRANCH" "origin/$BASE"
fi
log "at $(git rev-parse --short HEAD) of $BRANCH"

# --- Pieces of the prompt ------------------------------------------------------------------------

ticket() { jq -r '"Ticket \(.ticket.key): \(.ticket.title)\n\n\(.ticket.text // "")"' "$TASK"; }
answers() {
  jq -r 'if ((.answers // []) | length) > 0
    then "\n\nAnswers to the questions asked earlier:\n" + ([.answers[] | "- Q: \(.question)\n  A: \(.answer)"] | join("\n"))
    else "" end' "$TASK"
}
feedback() { jq -r 'if .feedback then "\n\nFeedback from the review, to address:\n" + .feedback else "" end' "$TASK"; }

# opencode <command> <arguments>: one unattended OpenCode run. Its JSON events go to the log for
# token counts and debugging.
opencode_run() {
  local started=$SECONDS
  log "opencode $1"
  opencode run --pure --format json --model "$MODEL" --command "$1" "$2" </dev/null \
    >/tmp/opencode.jsonl 2>/tmp/opencode.err ||
    fail "opencode exited with $?: $(tail -c 400 /tmp/opencode.err)"
  log "opencode took $((SECONDS - started)) s, $(jq -s '[.[] | select(.type == "step_finish")] | length' \
    /tmp/opencode.jsonl 2>/dev/null || echo "?") steps"
  log "the agent's last words: $(last_words)"
}

# The model's final message, for the log and for a failure's reason.
last_words() {
  # A log cut short (OpenCode stopped while writing) does not parse: then there are none.
  [[ -s /tmp/opencode.jsonl ]] || { echo "(none)" && return; }
  { jq -rs '[.[] | select(.type == "text") | .part.text // empty] | last // "(none)"' /tmp/opencode.jsonl 2>/dev/null ||
    echo "(none)"; } | tr '\n' ' ' | cut -c1-600
}

# The agent asks by writing .agent/result.json (AGENTS.md of the target project); the run then
# pushes nothing.
questions_asked() { [[ -f .agent/result.json ]] && jq -e '.status == "questions"' .agent/result.json >/dev/null 2>&1; }
answer_with_questions() {
  result "$(fitted 550 '{result: "questions", questions: ((.questions // [])[:6] | map(tostring | .[0:$n]))}' \
    .agent/result.json)"
  exit 0
}

run_checks() {
  local check
  while IFS= read -r check; do
    [[ -n $check ]] || continue
    log "check: $check"
    bash -c "$check" >/tmp/check.log 2>&1 || fail "check failed: $check: $(tail -c 600 /tmp/check.log)"
  done < <(jq -r '.checks[]?' "$TASK")
}

# commit_push <message> <pathspec...>: commits what the run changed within the pathspec, and only
# that, then pushes the branch. Changes outside it are left behind and listed.
commit_push() {
  local message=$1
  shift
  git add -A -- "$@"
  if [[ -n $(git status --porcelain --untracked-files=all | grep -v '^[MADRC] ') ]]; then
    log "left out, outside what this mode may change:"
    log "$(git status --porcelain --untracked-files=all | grep -v '^[MADRC] ' | sed 's/^/  /')"
  fi
  git diff --cached --quiet && fail "the run changed nothing it may commit; the agent's last words: $(last_words)"
  git commit --quiet -m "$message"
  git push --quiet origin "HEAD:refs/heads/$BRANCH"
  COMMIT=$(git rev-parse HEAD)
  log "pushed $COMMIT to $BRANCH"
}

done_with() { # done_with <result>
  result "$(jq -nc --arg r "$1" --arg commit "$COMMIT" --arg branch "$BRANCH" --arg change "$CHANGE" \
    '{result: $r, commit: $commit, branch: $branch, change: $change}')"
}

# --- Modes ---------------------------------------------------------------------------------------

case $MODE in
  propose)
    # The pull request of the spec holds the change and nothing else.
    opencode_run opsx-propose "$(printf 'Change name: %s\n\n%s%s%s' "$CHANGE" "$(ticket)" "$(answers)" "$(feedback)")"
    questions_asked && answer_with_questions
    [[ -d openspec/changes/$CHANGE ]] || fail "the run wrote no change openspec/changes/$CHANGE"
    openspec validate "$CHANGE" --strict >/tmp/validate.log 2>&1 || fail "openspec validate: $(tail -c 600 /tmp/validate.log)"
    commit_push "spec($KEY): $CHANGE" "openspec/changes/$CHANGE"
    done_with spec_ready
    ;;
  apply | fix)
    # The code goes into the same pull request; the change stays unarchived until the code is
    # approved. A run never changes the CI workflows, which hold the products' push token.
    # A fix after the change was archived takes the archive commit back first: the change is
    # archived again, as the last commit, once the fix is approved.
    if [[ $MODE == fix && $(task .unarchive) == true ]]; then
      archived=$(git log -1 --format=%H --fixed-strings --grep="docs($KEY): archive $CHANGE")
      [[ -n $archived ]] || fail "no archive commit of $CHANGE on $BRANCH to take back"
      git revert --no-edit "$archived" >/dev/null
      log "took back the archive commit ${archived:0:12}"
    fi
    if [[ $MODE == apply ]]; then
      opencode_run opsx-apply "$(printf '%s%s' "$CHANGE" "$(answers)")"
    else
      opencode_run opsx-apply "$(printf '%s%s%s' "$CHANGE" "$(answers)" "$(feedback)")"
    fi
    questions_asked && answer_with_questions
    [[ -d openspec/changes/$CHANGE ]] || fail "the change openspec/changes/$CHANGE is gone; it stays unarchived until the code is approved"
    run_checks
    if [[ $MODE == apply ]]; then message="feat($KEY): $CHANGE"; else message="fix($KEY): review of $CHANGE"; fi
    commit_push "$message" . ':(exclude).gitea' ':(exclude).agent'
    done_with code_ready
    ;;
  archive)
    # No model: OpenSpec merges the change's delta into the specs and moves it to the archive.
    openspec archive "$CHANGE" --yes >/tmp/archive.log 2>&1 || fail "openspec archive: $(tail -c 600 /tmp/archive.log)"
    openspec validate --all --strict >/tmp/validate.log 2>&1 || fail "openspec validate: $(tail -c 600 /tmp/validate.log)"
    commit_push "docs($KEY): archive $CHANGE" openspec
    done_with archived
    ;;
esac
