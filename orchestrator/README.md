# Orchestrator

The delivery loop's service (design decision 4 of `openspec/changes/delivery-loop/`). Webhooks
from Plane and Gitea go into a queue in Postgres. Each work item has one LangGraph thread, and
its state is kept in the same database. Agent runs are Kubernetes Jobs in `sdlc-agents`. The
service writes no code: it reads a request, checks it against the policy, and acts through the
tools' APIs or starts a run.

## Layout

```
src/orchestrator/
  main.py        entry point: `orchestrator migrate`, `orchestrator serve`
  config.py      settings: config.yaml (ORCHESTRATOR_CONFIG) overlaid with the environment
  db.py          the tables (inbox, thread_link, pending_runs) and LangGraph's checkpointer
  queue.py       the leased event queue: one event per thread at a time, in arrival order
  worker.py      the workers that take events from the queue
  links.py       thread_link: a ticket, a pull request -> their thread
  api.py         /healthz, /hooks/{plane,gitea}, the thread API (/threads...)
  adapters/      Plane and Gitea: payload -> Event, and the clients for the ports
  ports.py       the Tracker and CodeHost ports
  events.py      the normalized event
  projects.py    platform/projects/*/project.yaml: projects, people and roles
  policy.py      phases, the policy table, explicit signals
  intents.py     reading a request with one model call
  loop.py        the loop's rules, pure: an event and a state -> a new state and actions
  graph.py       the LangGraph thread (one step: decide, then act) and the dispatcher
  executor.py    agent runs as Jobs, at most max_active_runs at a time, and the watcher
tests/           pytest, against a real Postgres; fixtures/ holds scrubbed webhook payloads
```

## Tests

```bash
cd orchestrator
uv run --python 3.13 pytest
```

The queue's locking and the checkpointer need a real Postgres. Without `ORCHESTRATOR_TEST_DSN`,
the tests start a throwaway `postgres` pod in the namespace `orchestrator-test` of the
platform's cluster, reach it through a port-forward, and delete it at the end. Each test gets a
database of its own. Any Postgres the tests may create databases in works too, such as one more
throwaway pod (delete it with `kubectl delete pod orchestrator-pg -n orchestrator-test`):

```bash
kubectl create namespace orchestrator-test
kubectl run orchestrator-pg -n orchestrator-test --image=postgres:18.6-alpine3.24 --env=POSTGRES_PASSWORD=test
kubectl wait pod/orchestrator-pg -n orchestrator-test --for=condition=Ready
kubectl port-forward -n orchestrator-test pod/orchestrator-pg 55432:5432 &
ORCHESTRATOR_TEST_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres uv run --python 3.13 pytest
```

One test calls the real model provider and is skipped without `LLM_API_KEY`. CI
(`.gitea/workflows/images.yaml`) runs the tests against a Postgres in the runner before any image
is built.

## Local run

The service needs its tables first: `migrate` creates or updates them, and `serve` refuses to
start while any are missing. With the Postgres above:

```bash
export DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres
export ORCHESTRATOR_CONFIG=/dev/null PROJECTS_DIR=../platform/projects
uv run --python 3.13 orchestrator migrate
LLM_API_KEY=... uv run --python 3.13 orchestrator serve --port 8000
curl -s localhost:8000/healthz
```

Without `ORCHESTRATOR_COMMIT` or `AGENT_IMAGE`, agent runs are off: the service reads events and
answers, and an action that needs a run fails. Plane and Gitea are reached at their in-cluster
addresses by default. From the host, set `plane_url` and `gitea_url` in a config file to
`https://plane.sdlc.internal` and `https://gitea.sdlc.internal`, and give it `PLANE_API_KEY` and
`GITEA_TOKEN`.

## Configuration

`config.yaml` (the path is `ORCHESTRATOR_CONFIG`, `/config/config.yaml` by default) holds the
settings that are not secret. In the cluster, it comes from `config` in
[`platform/apps/orchestrator/chart/values.yaml`](../platform/apps/orchestrator/chart/values.yaml).

| Setting | Default | |
|---|---|---|
| `llm_base_url` | `https://routerai.ru/api/v1` | any OpenAI-compatible API |
| `intent_model` | `deepseek/deepseek-v4-flash-0731` | reads requests; `deepseek/*` gets ChatDeepSeek, others ChatOpenAI |
| `agent_model` | `llm/deepseek/deepseek-v4-flash-0731` | for agent runs; one of `platform/apps/agents/opencode-config.yaml` |
| `intent_threshold` | `0.7` | below it, the agent asks what was meant |
| `workers` | `2` | threads taking events |
| `max_attempts` | `5` | an event that killed a worker this often is closed |
| `max_active_runs` | `2` | agent runs at a time; the rest wait in `pending_runs` |
| `agent_namespace` | `sdlc-agents` | |
| `plane_url`, `gitea_url` | the in-cluster services | |

The environment holds the secrets and connections and overrides the file:

| Variable | In the cluster from |
|---|---|
| `DATABASE_URL` | the Secret `sdlc/orchestrator-env` |
| `LLM_API_KEY` | `sdlc/orchestrator-env` (from `secrets.env`) |
| `PLANE_WEBHOOK_SECRET`, `GITEA_WEBHOOK_SECRET` | `sdlc/orchestrator-env`; signatures are checked from the hardening group on |
| `PLANE_API_KEY`, `PLANE_PEOPLE` | `sdlc/plane-agent`, from Plane's setup Job |
| `GITEA_TOKEN` | `sdlc/gitea-agent`, from the bootstrap |
| `PROJECTS_DIR` | the ConfigMap `target-projects`; a changed file is read again without a restart |
| `ORCHESTRATOR_COMMIT` | the image tag; the agent image is `platform/agent-runner` of the same commit |
| `AGENT_IMAGE` | not set; overrides the agent image |

## How a change reaches the cluster

1. A push to `main` in Gitea starts `.gitea/workflows/images.yaml`: the tests, then
   `platform/orchestrator:<commit>` and `platform/agent-runner:<commit>`.
2. Argo CD syncs `platform/apps/orchestrator/` with `image.tag=$ARGOCD_APP_REVISION`, the same
   commit. Its PreSync Job runs `orchestrator migrate` with the new image, and then the
   Deployment rolls over. The old pod keeps serving until the new one is ready. If the build
   failed, the new pod waits for an image that never comes, and the old one keeps running.
3. Two pods at once are safe: events are leased, and the run limit is a lock in Postgres.

`platform/smoke.sh orchestrator_running` checks the rollout, `/healthz` and Gitea's
organization webhook. `plane_loop_project` checks Plane's webhook.

## The thread API

| | |
|---|---|
| `GET /threads?link=ticket:<project>/<key>`, `gitea_pr:<owner>/<repo>#<number>` or `plane_work_item:<project id>/<work item id>` | the thread of a ticket or a pull request, and its links |
| `GET /threads/{thread_id}` | its phase, key, branch, change, pull request and links |
| `GET /threads/{thread_id}/messages` | the conversation: people's requests and the agent's answers |

From the host: `kubectl port-forward -n sdlc svc/orchestrator 8000:80`; in a URL, `#` is `%23`.
