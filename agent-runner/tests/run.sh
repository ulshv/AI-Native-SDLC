#!/usr/bin/env bash
# agent-runner's tests: the entrypoint run for each mode against a throwaway git server (a bare
# repository on disk), with OpenCode and the OpenSpec CLI stubbed (tests/stubs), and each result
# checked as the orchestrator reads it. In the image's test stage (Dockerfile) and locally:
#   agent-runner/tests/run.sh
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENTRYPOINT=${ENTRYPOINT:-$here/../entrypoint.sh}
export PATH="$here/stubs:$PATH"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME=$tmp/home GITEA=file://$tmp/git TOKEN_FILE=$tmp/token STUB_CHANGE=ewa-1-filter
mkdir -p "$HOME" && echo token >"$TOKEN_FILE"
git config --global user.name test && git config --global user.email test@example.invalid
git config --global init.defaultBranch main

# The target project: main and development, as seed-projects.sh leaves it.
git init -q --bare "$tmp/git/sdlc/app.git"
git clone -q "$tmp/git/sdlc/app.git" "$tmp/seed" 2>/dev/null
(cd "$tmp/seed" && echo app >app.txt && git add . && git commit -qm seed && git push -q origin HEAD:main HEAD:development)

failures=0

# run <mode> <stub> [task fields as jq]: one run of the entrypoint; prints its result.
run() {
  local mode=$1 stub=$2 extra=${3:-.} dir
  dir=$(mktemp -d "$tmp/run-XXXX")
  mkdir -p "$dir/work"
  jq -n --arg mode "$mode" '{mode: $mode, repo: "sdlc/app", base: "development", branch: "feat/EWA-1_filter",
    change: "ewa-1-filter", model: "llm/test", ticket: {key: "EWA-1", title: "Filter", text: "Filter it."},
    checks: ["test -f app.txt"]}' | jq "$extra" >"$dir/task.json"
  STUB=$stub TASK=$dir/task.json WORK=$dir/work RESULT=$dir/result "$ENTRYPOINT" >"$dir/log" 2>&1 || true
  cat "$dir/result"
}

# expect <what> <jq test> <result>
expect() {
  if jq -e "$2" <<<"$3" >/dev/null 2>&1; then
    echo "ok    $1"
  else
    echo "FAIL  $1: $3"
    failures=$((failures + 1))
  fi
}

on_branch() { git --git-dir="$tmp/git/sdlc/app.git" "$@" refs/heads/feat/EWA-1_filter 2>/dev/null; }
# holds <path prefix>: whether the run's branch, as pushed, has files under it.
holds() {
  on_branch ls-tree -r --name-only >"$tmp/files" || true
  grep -q "^$1" "$tmp/files"
}

STUB_QUESTIONS=$tmp/q.json
export STUB_QUESTIONS
echo '{"status": "questions", "questions": ["Should the filter survive a reload?"]}' >"$STUB_QUESTIONS"

result=$(run propose questions)
expect "questions end the run and push nothing" \
  '.result == "questions" and .questions == ["Should the filter survive a reload?"]' "$result"
! on_branch rev-parse --verify --quiet >/dev/null || { echo "FAIL  questions pushed the branch" && failures=$((failures + 1)); }

jq -n '{status: "questions", questions: [range(6) | "Должен ли фильтр сохраняться после перезагрузки страницы? " * 12]}' \
  >"$STUB_QUESTIONS"
result=$(run propose questions)
expect "long non-ASCII questions fit the 4 KiB termination message" \
  "(.questions | length) == 6 and (tojson | utf8bytelength) <= 3900" "$result"

result=$(run propose crash)
expect "a failing OpenCode ends the run with its words" \
  '.result == "failed" and (.reason | test("provider refused the key"))' "$result"

result=$(run propose propose)
expect "propose pushes the change alone" '.result == "spec_ready" and (.commit | length) == 40' "$result"
holds openspec/changes/ewa-1-filter/ || { echo "FAIL  the change is not on the branch" && failures=$((failures + 1)); }

result=$(run apply nothing)
expect "a run that changes nothing fails" '.result == "failed" and (.reason | test("changed nothing"))' "$result"

result=$(run apply code)
expect "apply pushes the code" '.result == "code_ready"' "$result"
! holds .gitea/ || { echo "FAIL  the run pushed a change to .gitea" && failures=$((failures + 1)); }

result=$(run archive nothing)
expect "archive pushes the archived change" '.result == "archived"' "$result"

result=$(run fix code '. + {unarchive: true, feedback: "rename it"}')
expect "a fix after the archive takes it back first" '.result == "code_ready"' "$result"
holds openspec/changes/ewa-1-filter/ || { echo "FAIL  the change is not back after the fix" && failures=$((failures + 1)); }

result=$(run propose propose '. + {branch: "main"}')
expect "a run refuses to push to a branch that is not its own" '.result == "failed"' "$result"

echo "$failures failures"
((failures == 0))
