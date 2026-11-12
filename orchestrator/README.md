# Orchestrator

The delivery loop's service (design decision 4 of `openspec/changes/delivery-loop/`). Webhooks
from Plane and Gitea go into a queue in Postgres. Each work item has one LangGraph thread, and
its state is kept in the same database. Agent runs are Kubernetes Jobs in `sdlc-agents`. The
service writes no code: it reads a request, checks it against the policy, and acts through the
tools' APIs or starts a run.

## Layout

```
src/orchestrator/
  main.py        entry point: `orchestrator migrate`, `orchestrator serve`; settings and clients
  agent.py       the agent's root: its LangGraph threads, checkpoints, and the parts it is made of
  dispatch.py    delivery -> command: event, project, thread, and the request's intent
  machine.py     the loop's rules as tables (requests, run outcomes, after an archive, PR events)
                 and the step that acts through the ports
  model.py       the work item a thread keeps, and the commands of its steps
  policy.py      phases, rules (intent, phases, roles), explicit signals
  intents.py     reading a request with one model call
  llm.py         the model provider's clients, chosen by the model's prefix
  logs.py        JSON logs through structlog, with the ids bound to a delivery, thread or run
  metrics.py     the Prometheus metrics at /metrics
  config.py      settings: config.yaml (ORCHESTRATOR_CONFIG) overlaid with the environment
  db.py          the pool, `migrate` (Alembic) and the schema revision check
  migrations/    Alembic revisions of the tables (inbox, thread_link, pending_runs)
  queue.py       the leased event queue: one event per thread at a time, in arrival order
  worker.py      the workers that take events from the queue
  links.py       thread_link: a ticket, a pull request -> their thread
  api.py         /livez, /readyz, /metrics, /hooks/{plane,gitea}, the thread API (/threads...)
  adapters/      Plane and Gitea: payload -> Event, and the clients for the ports
  http.py        the clients' HTTP: timeouts, bounded retries (tenacity), errors naming their target
  ports.py       the Tracker and CodeHost ports
  events.py      the normalized event: its source, kind and what it is about (work item, PR, release)
  projects.py    platform/projects/*/project.yaml: projects, people and roles
  executor.py    agent runs as Jobs, at most max_active_runs at a time, and the watcher
tests/           pytest, against a real Postgres; fixtures/ holds scrubbed webhook payloads
```

A delivery goes through the dispatcher to a command, and the agent runs the command as one step
of the item's thread: the machine finds the rule, its handler acts through the ports, and the
checkpoint keeps the new item. A new rule of the loop is a row in a table of `machine.py` with a
small handler; another tracker or code host is an adapter behind the ports.

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

One test calls the real model provider and is skipped without `LLM_API_KEY`.

Lint, format and types, which CI runs before the tests:

```bash
uv run --python 3.13 ruff check src tests
uv run --python 3.13 ruff format --check src tests   # `ruff format src tests` fixes it
uv run --python 3.13 pyright                         # strict, the same checker as Pylance
```

CI (`.gitea/workflows/images.yaml`) runs all of it, and the tests against a Postgres in the
runner, before any image is built.

## Local run

The service needs its schema first: `migrate` brings it to the code's revision, and `serve`
refuses to start on a database behind it. It also refuses to start without its secrets. With the
Postgres above, and any values while nothing calls out:

```bash
export DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres
export ORCHESTRATOR_CONFIG=/dev/null PROJECTS_DIR=../platform/projects
uv run --python 3.13 orchestrator migrate
export LLM_API_KEY=... PLANE_API_KEY=... GITEA_TOKEN=... PLANE_WEBHOOK_SECRET=x GITEA_WEBHOOK_SECRET=x
uv run --python 3.13 orchestrator serve --port 8000
curl -s localhost:8000/readyz
```

Without `ORCHESTRATOR_COMMIT` or `AGENT_IMAGE`, agent runs are off: the service reads events and
answers, and an action that needs a run fails. Plane and Gitea are reached at their in-cluster
addresses by default. From the host, set `plane_url` and `gitea_url` in a config file to
`https://plane.sdlc.internal` and `https://gitea.sdlc.internal`, with real `PLANE_API_KEY` and
`GITEA_TOKEN`.

## Schema changes

The orchestrator's tables are Alembic revisions in `src/orchestrator/migrations/versions/`, one
file per change, forward-only (a mistake is fixed by the next revision). A revision must work
with the version still running during a rollout: add first, drop or rename in a later release.

```bash
export DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres
uv run --python 3.13 alembic revision -m "add a column" --rev-id 0003   # then write upgrade()
uv run --python 3.13 orchestrator migrate                               # what the PreSync Job runs
```

`serve` refuses to start on a database behind its code. A database a newer release migrated is
left as it is, and the older code runs on it: that is what makes a rollback to an older commit
work, and why every revision has to be expand-then-contract. LangGraph's checkpoint tables are
its own; `orchestrator migrate` sets them up too.

## Configuration

The settings are one typed model per command (`src/orchestrator/config.py`), checked at start: a
missing or malformed setting, or a key in the file that `serve` does not know, stops the process
with a message naming it. `config.yaml` (the path is `ORCHESTRATOR_CONFIG`, `/config/config.yaml`
by default) holds the settings that are not secret. In the cluster, it comes from `config` in
[`platform/apps/orchestrator/chart/values.yaml`](../platform/apps/orchestrator/chart/values.yaml).
Any setting can also come from the environment, named in capitals (`WORKERS=4`), which wins over
the file.

| Setting | Default | |
|---|---|---|
| `llm_base_url` | `https://routerai.ru/api/v1` | any OpenAI-compatible API |
| `intent_model` | `deepseek/deepseek-v4-flash-0731` | reads requests, at temperature 0; `deepseek/*` gets ChatDeepSeek, others ChatOpenAI |
| `agent_model` | `llm/deepseek/deepseek-v4-flash-0731` | for agent runs; one of `platform/apps/agents/opencode-config.yaml` |
| `intent_threshold` | `0.7` | below it, the agent asks what was meant |
| `workers` | `2` | threads taking events |
| `max_attempts` | `5` | an event that failed this often is given up (closed with its error) |
| `max_active_runs` | `2` | agent runs at a time; the rest wait in `pending_runs` |
| `agent_namespace` | `sdlc-agents` | |
| `plane_url`, `gitea_url` | the in-cluster services | |
| `gitea_public_url` | `https://gitea.sdlc.internal` | for the pull request links people open |

The environment holds the secrets and connections. `serve` needs every one of them, `migrate`
only `DATABASE_URL`:

| Variable | In the cluster from |
|---|---|
| `DATABASE_URL` | the Secret `sdlc/orchestrator-env` |
| `LLM_API_KEY` | `sdlc/orchestrator-env` (from `secrets.env`) |
| `PLANE_WEBHOOK_SECRET`, `GITEA_WEBHOOK_SECRET` | `sdlc/orchestrator-env`; a webhook without a valid signature gets 401 |
| `PLANE_API_KEY`, `PLANE_PEOPLE` | `sdlc/plane-agent`, from Plane's setup Job |
| `GITEA_TOKEN` | `sdlc/gitea-agent`, from the bootstrap |
| `PROJECTS_DIR` | the ConfigMap `target-projects`; a changed file is read again without a restart |
| `ORCHESTRATOR_COMMIT` | the image tag; the agent image is `platform/agent-runner` of the same commit |
| `AGENT_IMAGE` | not set; overrides the agent image |

## How a change reaches the cluster

1. A push to `main` in Gitea starts `.gitea/workflows/images.yaml`: the orchestrator's lint,
   types and tests, shellcheck on the scripts and the manifests' check, then
   `platform/orchestrator:<commit>` and `platform/agent-runner:<commit>` (after agent-runner's own
   tests in its image).
2. Argo CD syncs `platform/apps/orchestrator/` with `image.tag=$ARGOCD_APP_REVISION`, the same
   commit. Its PreSync Job runs `orchestrator migrate` with the new image, and then the
   Deployment rolls over. The old pod keeps serving until the new one is ready. If the build
   failed, the new pod waits for an image that never comes, and the old one keeps running.
3. Two pods at once are safe: events are leased, and the run limit is a lock in Postgres.

`platform/smoke.sh orchestrator_running` checks the rollout, `/readyz` and Gitea's
organization webhook. `plane_loop_project` checks Plane's webhook.

## Operating it

- **Network:** a NetworkPolicy lets only Plane's and Gitea's namespaces reach the pod, and the
  webhooks need their signatures. `kubectl port-forward` comes from the node and still works.
- **Probes:** `/livez` answers while the process does; `/readyz` while its database answers. A
  database outage takes the pod out of the Service and never gets it restarted.
- **Logs:** one JSON line per record, with the `delivery`, `thread` and `run` it is about, so one
  item can be followed: `kubectl logs -n sdlc deploy/orchestrator | jq 'select(.thread == "<id>")'`.
  Before an item has a thread, `thread` is the link of the object an event is about.
- **Metrics** for Prometheus at `/metrics`: `orchestrator_queue_depth`,
  `orchestrator_queue_oldest_seconds`, `orchestrator_runs_active`, `orchestrator_runs_waiting`,
  `orchestrator_events_given_up_total`, and `orchestrator_external_errors_total{target}`: event
  attempts and watch rounds that failed on a call to plane, gitea, the model or kubernetes. An
  error of the code's own is in the logs, not there. Nothing scrapes them yet; the NetworkPolicy
  will need a rule for a scraper.
- **Stopping:** on SIGTERM the workers take no more events and finish theirs within 45 s; what is
  still being handled then is released for the next pod to take at once, and the attempt counts.

## The thread API

| | |
|---|---|
| `GET /threads?link=ticket:<project>/<key>`, `gitea_pr:<owner>/<repo>#<number>` or `plane_work_item:<project id>/<work item id>` | the thread of a ticket or a pull request, and its links |
| `GET /threads/{thread_id}` | its phase, key, branch, change, pull request and links |
| `GET /threads/{thread_id}/messages` | the conversation: people's requests and the agent's answers |

From the host: `kubectl port-forward -n sdlc svc/orchestrator 8000:80`; in a URL, `#` is `%23`.
