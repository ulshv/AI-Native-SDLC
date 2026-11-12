# agent-runner

The image of one agent run (design decision 7 of `openspec/changes/delivery-loop/`). The
orchestrator starts each run as a Kubernetes Job in `sdlc-agents`; `platform/run-agent.sh` starts
one by hand. The model decides what to write; everything around it that needs no model is
`entrypoint.sh`: clone, branch, one OpenCode command, the project's checks, commit, push and one
result.

## Layout

```
Dockerfile      Node 24 with git and jq; OpenCode (opencode-ai) and the OpenSpec CLI, pinned;
                a `test` stage that runs the tests in the image
entrypoint.sh   one run, from /task/task.json to the result in /dev/termination-log
tests/run.sh    the entrypoint for every mode, against a throwaway git repository
tests/stubs/    OpenCode and the OpenSpec CLI as the tests stand them in
```

What a run uses from elsewhere: OpenCode's configuration (the provider and its models), the
service account and the NetworkPolicy in [`platform/apps/agents/`](../platform/apps/agents/); the
Job and its task from the orchestrator's `executor.py`; and, in the target project's clone, its
`AGENTS.md` and the `/opsx` commands OpenCode runs.

## A run

The task (`/task/task.json`, a ConfigMap the orchestrator writes):

```json
{"mode": "propose", "repo": "sdlc/example-webapp", "base": "development",
 "branch": "feat/EWA-12_orders-filter", "change": "ewa-12-orders-filter",
 "model": "llm/deepseek/deepseek-v4-flash-0731",
 "ticket": {"key": "EWA-12", "title": "...", "text": "..."},
 "answers": [{"question": "...", "answer": "..."}], "feedback": "...", "unarchive": false,
 "checks": ["npm ci", "npm test"]}
```

| Mode | What it does | Commits | Result |
|---|---|---|---|
| `propose` | `/opsx:propose` for the ticket, with the answers and feedback so far | only `openspec/changes/<change>/`, validated | `spec_ready` |
| `apply` | `/opsx:apply` of the change, then the project's `checks` | the code, never `.gitea/` or `.agent/`; the change stays unarchived | `code_ready` |
| `fix` | the same with the review's feedback; with `unarchive`, the archive commit is taken back first | the same | `code_ready` |
| `archive` | `openspec archive`, no model, then every spec validated | `openspec/` | `archived` |

A run pushes to its own `feat/` or `archive/` branch only, as `agent-runner`, which may merge
nothing. When the agent lacks information it writes its questions to `.agent/result.json` (the
target project's `AGENTS.md` asks it to), and the run ends with `questions` and pushes nothing.
Anything that goes wrong ends with `failed` and a reason, the agent's last words included.

The result is one JSON in the pod's termination message, which holds 4096 bytes: long texts are
cut, counting bytes, to keep it under 3900. The model provider's key is redacted from everything
the run prints.

## Tests

```bash
agent-runner/tests/run.sh
```

Each mode runs against a bare repository on disk, with OpenCode and the OpenSpec CLI stubbed, and
its result is checked as the orchestrator reads it. CI (`.gitea/workflows/images.yaml`) builds the
Dockerfile's `test` stage, which runs the same in the image, before the image itself; an image
whose runs fail them is never pushed. shellcheck covers the entrypoint and the tests.

## How a change reaches the cluster

A push to `main` builds `gitea.sdlc.internal/platform/agent-runner:<commit>` with the
orchestrator of the same commit, which starts its runs with that image. OpenCode and OpenSpec are
upgraded here, in the Dockerfile; OpenSpec stays at the version the target projects' `/opsx`
commands were generated with.
