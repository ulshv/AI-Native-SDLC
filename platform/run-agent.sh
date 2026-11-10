#!/usr/bin/env bash
# Starts one agent run by hand, the way the orchestrator does (design decision 7), waits for it
# and prints its result. For trying a model, a prompt or the image, and for debugging.
#   platform/run-agent.sh <project> <mode> <key> [options]
#     <mode>             propose | apply | fix | archive
#     <key>              the ticket's key, such as EWA-12; the branch is feat/<key>_<slug>
#     --title TEXT       the ticket's title (the slug comes from it)
#     --text TEXT        the ticket's description
#     --slug SLUG        instead of the one made from the title
#     --branch BRANCH    instead of feat/<key>_<slug>, such as archive/<key>
#     --feedback TEXT    review feedback, for propose and fix
#     --answers FILE     a JSON list of {"question", "answer"}
#     --model MODEL      default llm/deepseek/deepseek-v4-flash-0731 (listed in apps/agents)
#     --image-tag TAG    default: the commit origin/main points at (built by images.yaml)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export KUBECONFIG=$HOME/.kube/config
NS=sdlc-agents

usage() { sed -n '4,16s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
(($# >= 3)) || { usage >&2; exit 2; }
PROJECT=$1 MODE=$2 KEY=$3
shift 3
TITLE="" TEXT="" SLUG="" BRANCH="" FEEDBACK="" ANSWERS="[]"
MODEL=llm/deepseek/deepseek-v4-flash-0731
TAG=$(git rev-parse origin/main)
while (($#)); do
  case $1 in
    --title) TITLE=$2 ;;
    --text) TEXT=$2 ;;
    --slug) SLUG=$2 ;;
    --branch) BRANCH=$2 ;;
    --feedback) FEEDBACK=$2 ;;
    --answers) ANSWERS=$(jq -c . "$2") ;;
    --model) MODEL=$2 ;;
    --image-tag) TAG=$2 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift 2
done

FILE=platform/projects/$PROJECT/project.yaml
[[ -f $FILE ]] || { echo "error: no $FILE" >&2; exit 1; }
[[ $KEY =~ ^[A-Z][A-Z0-9]*-[0-9]+$ ]] || { echo "error: not a ticket key: $KEY" >&2; exit 1; }
[[ -n $SLUG ]] || SLUG=$(tr '[:upper:]' '[:lower:]' <<<"$TITLE" | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-40 | sed 's/-$//')
[[ -n $SLUG ]] || { echo "error: give --title or --slug" >&2; exit 1; }
key=$(tr '[:upper:]' '[:lower:]' <<<"$KEY")
BRANCH=${BRANCH:-feat/${KEY}_$SLUG}
CHANGE=$key-$SLUG
NAME=$key-$MODE-$(date +%s)

task=$(yq -o json -I0 '{repo: .repository.gitea, base: .repository.base, checks: .checks}' <"$FILE" |
  jq -c --arg mode "$MODE" --arg branch "$BRANCH" --arg change "$CHANGE" --arg model "$MODEL" \
    --arg key "$KEY" --arg title "$TITLE" --arg text "$TEXT" --arg feedback "$FEEDBACK" --argjson answers "$ANSWERS" \
    '. + {mode: $mode, branch: $branch, change: $change, model: $model,
      ticket: {key: $key, title: $title, text: $text}, answers: $answers}
      + (if $feedback == "" then {} else {feedback: $feedback} end)')

labels=$(jq -nc --arg key "$key" --arg mode "$MODE" '{"app.kubernetes.io/part-of": "agent-runs", "sdlc/key": $key, "sdlc/mode": $mode}')

# The run's limits and isolation (design decision 7): 30 minutes, no retry, kept an hour for
# reading, not root, no capabilities, read-only root file system, no Kubernetes token.
jq -n --arg name "$NAME" --arg ns "$NS" --arg image "gitea.sdlc.internal/platform/agent-runner:$TAG" --argjson labels "$labels" '{
  apiVersion: "batch/v1", kind: "Job",
  metadata: {name: $name, namespace: $ns, labels: $labels},
  spec: {backoffLimit: 0, activeDeadlineSeconds: 1800, ttlSecondsAfterFinished: 3600,
    template: {metadata: {labels: $labels}, spec: {
      serviceAccountName: "agent-run", automountServiceAccountToken: false, restartPolicy: "Never",
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000,
        seccompProfile: {type: "RuntimeDefault"}},
      containers: [{name: "agent", image: $image,
        env: [{name: "OPENCODE_CONFIG", value: "/config/opencode.json"}, {name: "HOME", value: "/home/agent"},
          {name: "LLM_API_KEY", valueFrom: {secretKeyRef: {name: "llm-api-key", key: "LLM_API_KEY"}}}],
        securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}},
        resources: {requests: {cpu: "200m", memory: "512Mi"}, limits: {memory: "2Gi"}},
        volumeMounts: [{name: "task", mountPath: "/task"}, {name: "config", mountPath: "/config"},
          {name: "gitea", mountPath: "/secrets/gitea"}, {name: "work", mountPath: "/work"},
          {name: "home", mountPath: "/home/agent"}, {name: "tmp", mountPath: "/tmp"}]}],
      volumes: [{name: "task", configMap: {name: $name}}, {name: "config", configMap: {name: "opencode-config"}},
        {name: "gitea", secret: {secretName: "gitea-agent-runner", items: [{key: "token", path: "token"}]}},
        {name: "work", emptyDir: {}}, {name: "home", emptyDir: {}}, {name: "tmp", emptyDir: {}}]}}}}' |
  kubectl apply -f - >/dev/null

# The task goes in a ConfigMap owned by the Job, so it goes when the Job does.
uid=$(kubectl get job -n "$NS" "$NAME" -o jsonpath='{.metadata.uid}')
jq -n --arg name "$NAME" --arg ns "$NS" --arg uid "$uid" --arg task "$task" --argjson labels "$labels" '{
  apiVersion: "v1", kind: "ConfigMap",
  metadata: {name: $name, namespace: $ns, labels: $labels,
    ownerReferences: [{apiVersion: "batch/v1", kind: "Job", name: $name, uid: $uid}]},
  data: {"task.json": $task}}' | kubectl apply -f - >/dev/null
echo "job $NS/$NAME: $MODE $KEY on $BRANCH (image $TAG)"

started=$SECONDS
while :; do
  status=$(kubectl get job -n "$NS" "$NAME" -o json | jq -r '[.status.conditions[]? | select(.status == "True") | .type] | join(",")')
  [[ $status == *Complete* || $status == *Failed* ]] && break
  sleep 5
done
echo "finished after $((SECONDS - started)) s ($status)"
kubectl logs -n "$NS" "job/$NAME" 2>/dev/null | grep '^agent-run:' | grep -v '^agent-run: result' || true
pod=$(kubectl get pods -n "$NS" -l "job-name=$NAME" -o name | head -1)
message=$(kubectl get -n "$NS" "$pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.message}' 2>/dev/null || true)
if [[ -n $message ]]; then jq . <<<"$message"; else echo "no result: the pod ended without one (see kubectl logs -n $NS job/$NAME)"; fi
