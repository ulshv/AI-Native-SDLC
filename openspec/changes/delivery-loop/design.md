# Design

> **TL;DR**
> - **Orchestrator, threads first:** the LangGraph thread is the main entity (Agent Protocol style API); tracker work items, PRs and chat threads are only linked to a thread (`thread_id, kind, ref`), and the work's phase lives in the thread's state, one thread per work item. Webhooks go into a queue table in Postgres behind an interface (NATS when several services consume events); each event runs its thread's graph one step, with idempotent side effects and no `interrupt()` (decisions 4, 6).
> - **Words in, rules decide:** one model call, through a client chosen by the model's prefix, turns a comment into an intent; a policy table over role and phase decides, and Gitea's branch protection has the final word on merges (decisions 5, 8).
> - **Agent runs are Jobs:** OpenCode with any OpenAI-compatible provider in a fresh clone, pushing as `agent-runner`, which cannot merge; the result comes back as a termination message (decision 7).
> - **A project is one directory:** `platform/projects/<name>/project.yaml` feeds both the orchestrator and Argo CD's ApplicationSets for dev, prod and previews (decisions 10, 11).
> - **Every image is built by CI and tagged with its commit:** the platform's on a runner limited to this repository, so the orchestrator deploys through GitOps like a product; the products' on their own runner; BuildKit runs in each runner pod's own user namespace under AppArmor and seccomp profiles of its own; both push to Gitea's registry, which k3s pulls directly (decisions 2, 3, 12).
> - **Spikes done (group 1):** every assumption held or got a recorded fix: OpenCode runs unattended, the resolver works with the VPN client, Plane needs no manual step, Gitea enforces the merge rules, previews come and go with their PRs, and CI builds and pushes without privileges; the host's AppArmor quirk got a profile fix instead of switching AppArmor off.
> - **Biggest risk:** the time (about 30–45 hours); the loop up to the merge into `development` comes first.

## Context

See `proposal.md` for the motivation and `specs/` for the required behavior. The target flow is
described for readers in the README ("How it works, by example", "Branches and environments")
and in `docs/decisions.md` D17–D23; this document is how to build it.

- **What runs today** (change `platform-core`): k3s on the laptop with a fixed node address
  (`10.254.0.1` on `sdlc0`); Argo CD app-of-apps from `platform/apps/`; Traefik with a
  `*.sdlc.internal` default certificate from the ClusterIssuer `local-ca`; a CoreDNS rewrite that
  sends every `(.+)\.sdlc\.internal` name to Traefik from pods; the host's names in a managed
  `/etc/hosts` block; one CloudNativePG cluster with a `Database` per service; Gitea 12.7 chart;
  Plane CE through kustomize, with an admin created by a Django-shell hook Job and
  `WEBHOOK_ALLOWED_IPS` for in-cluster webhooks; secrets in `~/.config/ai-native-sdlc/secrets.env`
  driven by `platform/secrets.manifest`; `bootstrap.sh` phases preflight → secrets → host →
  git hosting → repository → gitops → adoption.
- **Constraints:** one 32 GB laptop; the operator runs everything that needs sudo; the host's VPN
  client (Happ, TUN mode) intercepts DNS on port 53 and has broken networking before; CA trust
  inside pods was a non-goal of `platform-core`, so pods talk to Gitea and Plane over their
  in-cluster HTTP services; the end-to-end loop must work before previews and releases, because
  the time is short and the loop alone is already worth showing.

## Goals / Non-Goals

**Goals:**
- Every new piece follows an existing pattern: an app directory under `platform/apps/`, a line in
  `secrets.manifest`, a bootstrap phase step that checks before it changes, a smoke check.
- A target project is one directory under `platform/projects/`, read by both the orchestrator and
  Argo CD (decision 10).
- A change to the orchestrator reaches the cluster the same way a product change does: a push,
  a CI build tagged with the commit, a GitOps sync (decision 3).
- The orchestrator is deterministic around a small, swappable use of a model: the model reads
  intents and the agent runs write specs and code; phases, permissions and side effects are code.

**Non-Goals:**
- More than one orchestrator replica. The event queue is ready for it (decision 4), but one is
  enough here.
- A model gateway such as LiteLLM Proxy. It is the candidate for the Langfuse step (decision 5).
- A message broker such as NATS. The queue sits behind an interface that one can implement later
  (decision 4).
- Dead-lettering events that keep failing. At a few dozen events a day, a failed one is visible in
  the log and in its thread; retries stop being enough only with more traffic.
- Tracing (Langfuse), Mattermost, gating previews on CI, stale-approval checks, SSO (Keycloak, a
  proposal of its own, starting from `docs/identity-and-access.md`).
- Removing the `/etc/hosts` block from `platform-core`. The wildcard resolver comes on top of it.

## Decisions

### 1. Layout

```
orchestrator/                    the service: webhooks, event queue, graph, adapters, policy, executor
agent-runner/                    image for agent Jobs: Node, OpenCode, OpenSpec CLI, git, entrypoint
.gitea/workflows/images.yaml     builds the platform's images on every push to main (decision 3)
platform/seed-projects.sh        creates target project repositories from local clones (decision 9)
platform/projects/<name>/        one directory per target project, project.yaml first (decision 10)
platform/apps/orchestrator/      Deployment, ConfigMaps, RBAC, Database (namespace sdlc)
platform/apps/agents/            namespace sdlc-agents, ServiceAccount, NetworkPolicy, OpenCode config
platform/apps/ci/                two Actions runners with BuildKit sidecars (namespace ci)
platform/apps/environments/      ApplicationSets for dev, prod and previews of every project
platform/apparmor/, seccomp/     host profiles the bootstrap installs: containerd's fix, BuildKit's (decision 12)
~/Projects/sdlc/example-webapp   the product, a separate repository (decision 9)
```

### 2. Registry: Gitea's container registry, pulled by k3s directly

Images live in Gitea's built-in package registry, in two organizations: the platform's own
images at `gitea.sdlc.internal/platform/<name>:<commit>`, the target projects' at
`gitea.sdlc.internal/sdlc/<name>:<commit>`. The bootstrap's last phase writes
`/etc/rancher/k3s/registries.yaml` with the local CA and a read-only pull token for
`gitea.sdlc.internal`, and restarts k3s only when the file changed; it comes last because the
token needs Gitea. containerd on the host resolves the name through `/etc/hosts` to Traefik, so
pulls work offline, and no namespace needs an `imagePullSecret`.

- **Who may push and pull (spike 1.6):** packages belong to the organization, not to a
  repository, so a repository collaborator gets `401`, and write access covers every package of
  the organization. The pull account `reader` is in a read team of both organizations; each
  CI account is in a write team of one (decision 12). Gitea Actions' own job token is refused by
  the registry, so CI pushes with a token of its own. `registries.yaml` names the local CA as
  `ca_file`.
- **Why two organizations:** a target project's workflow is written by an agent and runs on
  every push, before review, on a runner registered on the whole `sdlc` organization. Anything
  granted on `sdlc` is within its reach: the organization's runner registration token (a job
  could register a runner of its own with any label), its teams and its CI token. So this
  repository moved to `platform/ai-native-sdlc` too, next to the platform's images; the
  bootstrap transfers an existing `sdlc/ai-native-sdlc` there with its history, and Gitea
  keeps redirecting the old path. Found in the code review of group 3.
- **Alternatives:** a separate `registry:2` (one more component with its own auth and storage);
  pushing to Gitea's in-cluster HTTP service (the image name would differ from the pull name, and
  Gitea's token realm redirects to `https://gitea.sdlc.internal` anyway).

### 3. Every image is built by CI and tagged with its commit, the platform's on its own runner

The orchestrator will change often, so it is built and deployed like a product. On every push to
`main` of this repository, `.gitea/workflows/images.yaml` builds `orchestrator` and `agent-runner`
and pushes both as `gitea.sdlc.internal/platform/<name>:<commit>`, with a token that is a secret
of this repository alone. The orchestrator's Application passes `$ARGOCD_APP_REVISION` to
its manifests as the tag of both images, so a push to `main` rolls the orchestrator out within
minutes, and the Jobs it starts use the agent image from the same commit. The rule is the same as
for products (D19), and a commit SHA is an exact version, as `gitops-reconciliation` requires.

- **A separate runner for the platform:** the products' runner executes agent-written code, so it
  must never build the orchestrator. The platform's runner is registered on this repository only
  and has its own label (`platform`); the products' runner is registered on the organization with
  the label `ubuntu-latest` (decision 12). This repository's workflows use only `platform`.
- **Every commit restarts the orchestrator,** docs included. That is harmless by design: a
  restart loses nothing (decision 4), and the rollout keeps the old pod until the new one is
  ready, so a commit whose build failed leaves the running version alone.
- **After a rebuild,** the bootstrap's push of `main` queues an `images` run before the runner
  exists; Gitea keeps it queued, and the runner picks it up once Argo CD has started it.
- **Alternatives:** building on the host with Podman and pinning a tree-hash tag in git (a manual
  step on every orchestrator change); Argo CD Image Updater (another controller and write-back
  commits).

### 4. Orchestrator: threads first, webhooks into a queue, one graph step per event

The orchestrator's main entity is the **LangGraph thread**, as in LangGraph's Agent Protocol
(threads, runs, state). Everything outside, such as a tracker work item, a pull request or a chat
thread, is only linked to a thread:

```
thread_link   thread_id, kind, ref        UNIQUE(kind, ref)
                plane_work_item   <project uuid>/<work item uuid>
                gitea_pr          sdlc/example-webapp#34
                mattermost_thread <thread id>                    (later)
                <any adapter's kind>, such as a Jira issue behind MCP   (later)
```

- **The thread's state holds the work:** the checkpoint keeps the messages, the key (`EWA-12`,
  taken from the source), the phase, the branch, the change name, the preview address and pending
  questions. The policy (decision 5) reads the phase from there.
- **One thread per work item:** intake creates the thread and links the tracker work item; opening
  the PR links the PR to the same thread. The agent sees the ticket and the review in one place.
  With `UNIQUE(kind, ref)`, every outside object belongs to exactly one thread, so routing is a
  lookup and no locks between threads are needed.
- **Routing:** an event carries the kind and ref of what it is about; the link table gives the
  thread, and an assignment of an unlinked work item creates a new thread. A Job's result returns
  to the thread that started it (its `thread_id` is a Job label); a published release goes to the
  thread of each listed work item.
- **Later, threads that only refer:** a Mattermost question "what is the status of EWA-12?" would
  get its own thread that reads the working thread's state through a lookup by link. That needs a
  `role` column (`works_on | refers_to`), uniqueness only for `works_on`, and a tool for reading
  another thread. All of it is left for the change that brings Mattermost.
- **Refs are stable ids:** a Plane project's identifier can be renamed, so the link stores UUIDs,
  and the human key lives in the thread's state. Branch and preview names are built from the key.

```
Plane / Gitea --> POST /hooks/{plane,gitea} --> INSERT inbox (source, delivery_id UNIQUE) --> 200
Job watcher (k8s watch on agent Jobs) ------------> INSERT inbox (job, job name, thread_id)
worker: take the oldest unprocessed row for a thread not being handled (FOR UPDATE SKIP LOCKED)
   --> adapter: raw payload -> Event{link kind, link ref, actor, type, text}; drop the agent's own
   --> thread_link lookup (or a new thread on intake); resolve the person
   --> LangGraph graph for that thread_id, state from the Postgres checkpointer
       router(phase, event) -> one handler -> side-effect nodes -> END
```

- FastAPI for the endpoints; one worker loop in the same process; Postgres database
  `orchestrator` in the shared cluster for the inbox, `thread_link` and LangGraph's
  checkpoints.
- **Migrations are a step of their own:** the orchestrator's tables are Alembic revisions,
  forward-only and expand-then-contract; `orchestrator migrate` (those and LangGraph's
  checkpoint tables) runs as an Argo CD PreSync Job with the same image before every rollout.
  The service never changes the schema: it refuses to start on a database behind its code, and
  runs on one a newer release migrated, which is what makes a rollback work. CI runs the tests
  but never reaches the database.
- **Standard parts** (the engineering standards in `AGENTS.md`): settings are one pydantic-settings
  model per command, checked at start, with every secret required; calls to Plane, Gitea, the
  model and Kubernetes have timeouts, and tenacity retries only what is safe to send again (429
  and refused connections always, server errors and lost answers for idempotent methods),
  within 120 s a call; webhook payloads are parsed into pydantic models and events are typed
  (their kind, and the work item, pull request or release they are about); Pyright strict, the
  checker Pylance runs in the editor, and ruff gate CI.
- **Operating it:** JSON logs through structlog with the delivery, thread and run bound to every
  line; `/livez` (the process) apart from `/readyz` (its database), so a database outage takes
  the pod out of the Service without restarting it; on SIGTERM the workers stop in the app's
  lifespan, finish within 45 s and release what is still leased; Prometheus metrics at
  `/metrics` for the queue, the runs and failed calls to the outside.
- **The queue is a table, behind an interface.** Events and the graph's checkpoints live in one
  database (the inbox pattern, as in Oban, graphile-worker or pgmq). Duplicates are caught by
  the unique delivery id for as long as the row exists, while Gitea can redeliver a webhook days
  later. A worker leases an event instead of holding a lock while it handles it, so a handler may
  touch the queue (an intake moves the item's waiting events to its new thread); a lease that
  runs out (the process died) makes the event available again. Effects are at least once: the
  graph keeps the deliveries it applied in the thread's state and skips one it sees again, and a
  handler routes an event anew when its object got a thread after the event was recorded.
  Found in the code review of group 7.
- **Why not NATS JetStream:** two systems to keep consistent, an ack on top of a database
  commit, and deduplication still in the database, since JetStream's window is two minutes by
  default; all for a few dozen events a day. NATS becomes the right tool when several services
  consume the same events (a separate Mattermost adapter, triage workers, auto-tickets); then the
  queue interface gets a NATS implementation, and nothing else changes.
- **The graph runs to END on every event** (D21). No `interrupt()`: an
  event can arrive at any time from any channel, and a resumed node would re-run its side effects.
- **Side effects are idempotent:** Job names are `<key>-<mode>-<n>` (an existing Job means
  "already started"); a PR is looked up by branch before it is created; every comment carries a
  hidden marker with the event id, so a retried event does not comment twice.
- **One thread at a time:** events for the same thread are applied in order; different threads
  run in parallel up to the worker count.
- **API, Agent Protocol style:** `GET /threads/{thread_id}` (state and links),
  `GET /threads/{thread_id}/messages`, and `GET /threads?link=<kind>:<ref>` to find a thread by
  what it is linked to.
- **Restart:** unprocessed inbox rows are simply picked up again; the Job watcher lists existing
  Jobs on start and enqueues results it has not recorded, then watches on from that listing's
  version, listing again only after an error.
- **CI before an image:** the orchestrator's lint, types and tests; shellcheck on every script;
  the manifests Argo CD applies from this repository, rendered and checked with kubeconform (the
  external charts are not rendered: their values are not manifests, and a chart takes unknown
  values silently); and agent-runner's tests, a stage of its Dockerfile that runs its entrypoint
  for every mode with OpenCode and OpenSpec stubbed.
- **Alternatives:** LangChain DeepAgents (`create_deep_agent` with its middleware), dropping to
  LangGraph only where the middleware falls short. It is a harness for an agent that loops and
  picks its own next tool, while the orchestrator is a state machine with one model call to read
  an intent. With merges and comments as an agent's tools, the model would decide whether to call
  them, against decision 5; a loop with any number of tool calls is harder to make safe for a
  redelivered event than fixed side-effect nodes with markers; and its human-in-the-loop
  middleware is built on `interrupt()`, which D21 rules out. Its planning, file system, sub-agents
  and context summarizing are what OpenCode already does inside the Jobs. A deep agent compiles to
  a LangGraph graph, so it can still become one node of this graph where open-ended reasoning
  pays off, such as the Mattermost status threads or triage, without changing the core.

### 5. Reading intents and deciding what is allowed

- **Intent:** one model call through the configured OpenAI-compatible provider (the author uses
  RouterAI) with a JSON schema answer `{intent, confidence}`. Input: the comment, the item's phase
  and the agent's last message. Below confidence 0.7, or for `none`, the agent asks what is meant.
  The model and threshold are configuration. Reading is classification, so it runs at
  temperature 0: the same comment reads the same way every time.
- **Answers in the agent's own words (task 7.29):** a question that asks for no action ("what is
  the status?", "what is left before the merge?") is the intent `ask`, and the agent answers it;
  a comment read as no request gets a reply that says so and what fits. A second model call,
  with the same model at temperature 0.3, writes either reply from facts the code hands it: the
  item's phase, ticket, pull request and run, the questions it waits on, the last messages, and
  what this person may ask for now, which the policy's table gives. The reply only informs: the
  prompt forbids promising an action, and nothing happens because of a reply. Before, both got
  one fixed sentence, which answered no question and said nothing about the phase. In Needs
  info, a mention that is clearly a question is answered; anything else is still the answer the
  agent waits for.
- **The model client is chosen by the model's prefix:** `deepseek/*` gets LangChain's
  `ChatDeepSeek`, anything else `ChatOpenAI`, both pointed at the configured base URL. The API is
  the same, but `ChatOpenAI` drops DeepSeek's reasoning blocks, which breaks multi-turn calls with
  tools; one map in code keeps that out of the graph.
- **LiteLLM Proxy, later:** one gateway in the cluster for the orchestrator, OpenCode and the
  target apps would give cost per project and a Langfuse callback, and Jobs could hold a virtual
  key with a budget instead of the real one. It fits the Langfuse step; here it would be a router
  in front of a router.
- **Explicit signals** skip the model: Plane status Approved → `approve`, Closed → `reject`;
  Gitea review "approved" → `approve` (of the spec or, in Code review, of the code),
  "request changes" → `request_changes`.
- **Policy** is a table in code:

| Intent | Allowed in phase | Allowed for |
|---|---|---|
| `approve` | Spec review | product owner, engineer |
| `approve` (the code) | Code review | engineer |
| `request_changes` | Spec review, Code review | product owner, engineer |
| `reject` | any open phase | product owner, engineer |
| `answer` | Needs info | any project member |
| `merge` | Code review | engineer |
| `archive` | On dev, change not archived | engineer |
| `prepare_release` | any (project-level) | product owner, engineer |
| `ask`, or no clear request | any | any project member, even while a run goes on |

- **People** are listed in each project file with their Plane and Gitea usernames and roles.
  Usernames only, no emails, because this repository is public. Unknown accounts get a polite
  refusal.
- **Merges** go through the Gitea API with the `agent` account. Gitea's branch protection is the
  final word (decision 8), so a policy bug still cannot merge into `main`.

### 6. Adapters for Plane and Gitea, behind ports

The core talks to the outside through two ports: `Tracker` (read a work item, comment or reply in
a conversation, set a status) and `CodeHost` (branches, PRs, replies in reviews, merges, webhook
registration), plus an inbound normalizer per source. Plane and Gitea are the first
implementations. A source reached through MCP can implement the same ports later; MCP servers
rarely push events, so such an adapter would poll or use notifications, which the core does not
need to know.

- **Plane:** webhooks for work items (assignees, state) and comments. A mention is recognised by
  the agent's user id in the comment's mention markup (format confirmed by a spike). The agent
  uses its own API key to set states, comment and read the item. Replies go to the item.
  Spike 1.3: the body is `{event, action, workspace_slug, data, activity}`; `activity` holds the
  changed `field`, old and new values and the `actor`; an assignment is `issue`/`updated` with
  field `assignees`, a status change has field `state`; a comment carries
  `<mention-component entity_identifier="<user id>">` in `comment_html` and the author's id in
  `actor`. The work item has `sequence_id` and the project's UUID but not its identifier, so the
  key (`EWA-12`) is built with the identifier from `project.yaml`. `X-Plane-Delivery` is the
  delivery id for deduplication.
- **Gitea:** repository webhooks for `pull_request` (opened, synchronized, closed with `merged`),
  the review events (approved, rejected, comment), `issue_comment` on PRs and `release`
  (published), from one webhook on the organization `sdlc`, which the bootstrap registers as the
  admin (`agent` has no admin rights on the repositories). Replies go to the PR. Mentions are the
  literal `@agent`. Spike 1.4: a review event carries the
  review's text and the pull request's head (its own `commit_id` is empty in Gitea 1.27) but
  neither the review's id nor its inline comments,
  so the adapter reads the PR's latest review by that person and its comments through the API.
  An approving review whose text mentions `@agent` is both an approval and a request to read.
  `html_url` follows the host a request came through, so links are built from configuration.
  `X-Gitea-Delivery` is the delivery id. Gitea's default `webhook.ALLOWED_HOST_LIST` allows only
  external hosts; the platform allows the cluster's pod and service networks, as for Plane.
- **Payloads carry people's e-mail addresses.** Test fixtures are scrubbed before they enter
  this public repository.
- **Echo:** an event whose actor is the agent's Plane user or Gitea's `agent` or `agent-runner`
  is recorded and dropped.
- **Signatures:** Plane's `X-Plane-Signature` and Gitea's `X-Gitea-Signature` are HMAC-SHA256
  over the raw body with generated secrets, hex-encoded (checked against the live Plane). A
  webhook without a valid signature gets 401 and is not recorded; without a configured secret,
  none is accepted. A NetworkPolicy lets only Plane's and Gitea's namespaces reach the
  orchestrator at all.

### 7. Agent runs: one Kubernetes Job per run

- **Image `agent-runner`:** Node LTS, pinned OpenCode and OpenSpec CLI, git, and an entrypoint
  that owns everything deterministic: clone over Gitea's in-cluster HTTP service, branch, run
  OpenCode, check, commit, push, report.
- **Inputs:** a per-run ConfigMap with `task.json` (repo, base, branch, change name, mode
  `propose | apply | fix | archive`, ticket text, questions and answers, feedback); the OpenCode
  config from `platform/apps/agents/` (one OpenAI-compatible provider block, RouterAI for the
  author, no plugins); Secrets with `LLM_API_KEY` and the `agent-runner` token. The model comes
  from the orchestrator's config through `opencode run --model`.
- **Modes:** `propose` runs `/opsx:propose` for the change; `apply` runs `/opsx:apply`, then the
  entrypoint runs the project's tests and pushes the code, leaving the change unarchived; `fix` is
  `apply` with the reviewer's feedback in the prompt; `archive` uses no model at all: the entrypoint
  runs `openspec archive <change> --yes`, validates the specs, and pushes that one commit. It runs
  after the code is approved, so the archive commit is the last one before the merge. The target
  repository's agent instructions say the run is unattended: never ask in chat; when information
  is missing, write the questions to `.agent/result.json` and stop.
- **Merging after an archive:** when the approval was a request to merge, the orchestrator merges
  as soon as the `archive` run reports `archived`. If a person merged in Gitea before the archive
  commit, the orchestrator only comments in the ticket (and later in chat) that the specs in PR
  #<n> on <key> were not archived and asks for an archive PR. When an engineer answers
  "@agent ok, create archive PR" (intent `archive`), it runs `archive` on a new branch
  `archive/<key>` from `development` and opens a PR with only that commit.
- **One run per item at a time:** the thread remembers the run going on and takes only that run's
  result. A request that needs another run while one goes gets "ask again when it is done";
  closing the item or merging its PR cancels the run (out of the pending queue, its Job deleted),
  and a result that arrives after that is dropped. Runs keep counting when a closed item is
  assigned again, so their names stay new.
- **A failed run** moves the item to Needs info with the reason; the next comment mentioning
  `@agent` runs the same mode again, with whatever the person wrote added as an answer. An item
  already on dev (a failed archive PR run) stays there, and asking for the archive again retries.
- **A run's result that cannot be acted on** (Gitea refuses the merge, Plane is down) fails its
  attempts like any event; on the last one the thread stops waiting for that run, which has
  ended, and tells the people why, so they can act again.
- **The status follows the work:** a status a person sets by hand that is no request is set
  back to the item's phase.
- **Changes after the archive:** a request for changes after the `archive` run takes the archive
  commit back (`git revert` in the `fix` run) before the fix, so the next approval archives
  again and the archive commit stays the last one before the merge.
- **Running OpenCode (spike 1.1):** `opencode run --pure --format json --model <provider>/<model>
  --command opsx-propose|opsx-apply "<arguments>" </dev/null`, with an `@ai-sdk/openai-compatible`
  provider block whose key is `{env:LLM_API_KEY}`. On a small change with DeepSeek V4 Flash
  through RouterAI: propose 85 s and about 0.9 RUB, apply 41 s and about 0.7 RUB, a vague task
  36 s ending in a questions file. The JSON events carry token counts per step; the cost comes
  from the provider's prices, since OpenCode's config holds none.
- **Built in group 6:** the first real runs on `example-webapp` with DeepSeek V4 Flash: propose
  101 s, apply 188 s with its checks, archive 15 s, and a vague ticket ended in two sensible
  questions after 52 s. One apply stopped after 8 steps having changed nothing, so the entrypoint
  ends such a run with `failed` and the agent's last words, and a person can have it run again. A provider key written into `secrets.env` with quotes broke OpenCode's config, whose
  parse error quoted the key into the pod's status: the entrypoint now redacts the key from
  everything it writes, and the bootstrap's preflight refuses quoted values.
- **Run in group 8:** EWA-2 "Orders: filter by status" went from its assignment to the merge
  into `development` in 8 min 46 s: propose 2 min 38 s, apply 2 min 31 s, archive 6 s, the rest
  the people's turns; every status and comment as the specs say, the archive commit last before
  the merge. A vague ticket ("We need an export.") got a spec built on guesses, not questions; asked
  to ask instead, the agent put three questions, and the answers reached the next run.
- **The model, compared after group 8** on the same tickets of `example-webapp`: DeepSeek V4
  Flash, Qwen3.8 Omni Flash and DeepSeek V4.1 Flash all wrote working code (every check green)
  and read every intent right. V4.1 Flash wrote the spec fastest (70 s against about 3 min), its
  code had the most tests, and it alone asked, as the target project's `AGENTS.md` wants, when a
  ticket said too little ("We need an export."); the others built the spec on guesses. Its apply
  takes about 4 min, 1.5 min more than V4 Flash. Both the agent runs and the reading of intents
  use it now.
- **Result:** the entrypoint writes one JSON to `/dev/termination-log` (`spec_ready`,
  `code_ready`, `archived`, `questions`, `failed`, plus the pushed commit). The watcher reads it from the
  pod status, so no log parsing and no extra storage.
- **Limits:** `activeDeadlineSeconds: 1800`, `backoffLimit: 0`, `ttlSecondsAfterFinished: 3600`;
  at most two active runs, the rest wait in the orchestrator's queue of pending runs.
- **Network:** a NetworkPolicy in `sdlc-agents` allows egress to cluster DNS, Gitea's pods on
  port 3000, and `0.0.0.0/0` except the pod and service networks (the model provider). k3s
  enforces NetworkPolicies with its built-in controller.
- **RBAC:** the orchestrator's ServiceAccount may create, watch and delete Jobs and ConfigMaps
  and read pods in `sdlc-agents`; nothing else.
- **Pod security:** non-root, `seccompProfile: RuntimeDefault`, all capabilities dropped, no
  privilege escalation, and containerd's default AppArmor profile. OpenCode is a Bun binary,
  which suspends its own threads with signals; on this host's kernel that needs the profile fix
  from the bootstrap (Risks), not an `Unconfined` override. OpenCode's `question` tool is allowed
  by default and would wait forever in a Job, so the agent config denies it, along with
  `doom_loop`, `external_directory`, `webfetch` and `websearch` (spike 1.1).

### 8. Gitea accounts and branch protection

The bootstrap creates `agent` and `agent-runner` through Gitea's admin API, with tokens it stores
only in the cluster (like the Argo CD and push tokens of `platform-core`), plus a read-only
`registry-pull` token for k3s. In each target repository, set up by `seed-projects.sh`
(decision 9): `agent` and `agent-runner` get write
access; `development` disallows pushes and allows merges only by the engineers and `agent`, with
no required review in Gitea: the gate there is a person's request checked by the policy
(decision 5), and an approval given as a comment would not count as a Gitea review anyway;
`main` disallows pushes, requires one approving review by a person, and allows merges only by the
people on the project's merge allowlist. `agent-runner` is on no merge allowlist, so a Job cannot merge, and
nothing but people can reach `main`.

Spike 1.4 confirmed each rule through the API, with one addition: "a review by a person" needs
Gitea's approvals allowlist (`enable_approvals_whitelist` with the project's people), because
otherwise an approval by any account with write access counts, `agent-runner` included. Gitea
also refuses an approval of one's own pull request, so `agent` cannot approve the release PR it
opened.

### 9. `example-webapp` lives in its own clone, seeded by its own script

The product's local clone is at `~/Projects/sdlc/example-webapp`; each project file names its
clone path, and an environment variable can override it. `platform/seed-projects.sh` reads
`platform/projects/*/project.yaml` and, for each project whose repository is missing in Gitea, creates it
from the clone, pushes all branches and tags, grants access and applies branch protection
(decision 8). An existing repository is left alone; a missing clone stops the script before it
changes anything.

- **Why not in `bootstrap.sh`:** the bootstrap builds the platform; products come and go on top
  of it. A separate script lets a project be added without re-running the platform's steps, and
  it needs no sudo, only the Gitea API. After a full rebuild the operator runs `bootstrap.sh`,
  then `seed-projects.sh`.

The seed is a Vite + React + TypeScript app: a login form that accepts `demo` / `demo`, a
dashboard with an orders table from a local JSON file, Vitest tests, a static nginx image, a Helm
chart in `deploy/chart`, CI and release workflows in `.gitea/workflows/`, and OpenSpec
initialised for OpenCode with specs `auth` and `dashboard`. `main` starts without a version tag
and `development` branches from it, so the first demo release is `v0.1.0` by the default rule
(decision 15). The README's example says `v1.5.0` → `v1.6.0`; it is updated with the other docs.

This repository's apply touches that second repository; its commits are made there the same way,
reviewed by the operator.

### 10. A target project is one directory

`platform/projects/example-webapp/project.yaml` holds the tracker project key and id, the
repository, the base branch, the clone path, the domain (`example-webapp.sdlc.internal`), the
merge allowlist, and the people with their usernames and roles. The orchestrator gets every
`project.yaml` through a kustomize `configMapGenerator`; Argo CD's ApplicationSets read the same
files with the git files generator (decision 11). Adding a project means adding a directory and
its local clone.

**People's accounts are the platform's, their roles the project's (task 7.28).** An account is
an identity: a username, a name, an e-mail address, a password. It lives in
`~/.config/ai-native-sdlc/accounts.yaml`, next to `secrets.env` and backed up with it, because
the repository is public and holds no addresses; `platform/accounts.sh` makes Plane and Gitea
match it, with one username in both tools. Its fields are Keycloak's, so the SSO step takes the
file as it is. A role is authorization: whose request the agent follows, whose approval counts,
who merges. Until SSO it stays in the project file's `people`, in git, reviewed and with history,
where the orchestrator gets it through GitOps. Plane CE sends no mail here and has no page to set
a password, so the script gives a new account a random first password and shows it once, the
same in both tools, and `--reset` gives a new one. Plane users need its Django shell, as in
decision 14. A user created, enabled or disabled changes the projects' members and
`people.json`, which Plane's setup Job makes after a sync, so the script has Argo CD sync Plane
once more. The instance admins only administer (and operate the platform until SSO): the author
works in the loop as `sergey`, so a person's requests and approvals are never an admin's. The
Gitea chart gives its admin a placeholder address; the bootstrap sets `GITEA_ADMIN_EMAIL`, out of
git. Finer roles, platform roles and one sign-in wait for SSO with Keycloak, a proposal of its
own; `docs/identity-and-access.md` holds what it starts from.

The directory leaves room for more per project without new conventions: for example
`values-dev.yaml`, `values-prod.yaml` or `values-preview.yaml` that the ApplicationSets pass to the
product's chart as a second source with `ignoreMissingValueFiles`, so the platform can override
an environment without a commit in the product's repository.

### 11. Environments with ApplicationSets

- **dev and prod:** one ApplicationSet, git files generator over `platform/projects/*/project.yaml`
  crossed with a list `[dev → development, prod → main]` (the branches are the project file's
  `repository.base` and `repository.release`). Each Application deploys the project's
  `deploy/chart` at that branch, with `image.tag=$ARGOCD_APP_REVISION` (D19), into
  `<project>-<env>`.
- **Previews:** one ApplicationSet with a matrix of the git files generator and the
  `pullRequest` generator for Gitea (in-cluster API URL, a token with `read:repository` and
  `read:issue`, `labels: [preview]`, `requeueAfterSeconds: 60`). With `goTemplate`, the preview id
  is `regexFind "[A-Z]+-[0-9]+" .branch | lower`, the host is `<id>.qa.<domain>`, the image tag is
  `.head_sha`, and the Helm release name is the id. Closing the PR drops it from the generator,
  and Argo CD deletes the Application and its resources (spike 1.5: about 20 s).
- **One preview namespace per project,** `<project>-preview`. Argo CD never deletes a namespace it
  created with `CreateNamespace`, so a namespace per preview would stay behind empty after every
  PR, with the TLS Secrets that cert-manager made in it. Release names keep previews apart, and
  cert-manager's `enableCertificateOwnerRef` removes a preview's Secret with its Certificate.
- **The ApplicationSet controller** ran with zero replicas in `platform-core`; it is now on.
- **An AppProject per project.** The chart comes from the product's repository, which agents
  write to, and a preview deploys a branch no person has reviewed. So the platform, not the
  chart, makes each project's AppProject and namespaces (an ApplicationSet over the same project
  files and a small chart of the platform's): the AppProject admits that repository only, the
  project's own `<project>-<env>` namespaces only, and nothing cluster-scoped. Its namespaces
  enforce the restricted Pod Security Standard and cap the number of pods. All of it lives in
  `platform/apps/environments/`.
- **What a chart may not do (task 9.3).** Traefik serves every namespace, so an Ingress could
  claim `gitea.sdlc.internal` or another project's host. A ValidatingAdmissionPolicy (built into
  Kubernetes, no Kyverno) admits an Ingress in a project's namespace only for the host in the
  namespace's `sdlc.internal/host` annotation, or under its `sdlc.internal/host-suffix`
  (`qa.<domain>` for previews), with no default backend. The AppProject admits only a whitelist
  of kinds (workloads, Services, ConfigMaps, Secrets, Ingresses, PVCs, HPAs, PDBs): a
  NetworkPolicy of the chart's own would open what the platform's closes, and Traefik's
  IngressRoutes, Gateway routes and cert-manager's resources would bypass the host check. Each
  namespace's NetworkPolicy admits only Traefik (and, in previews, the orchestrator, for
  `/version.json`) and lets pods reach DNS and the internet, nothing private.
- **The label and the QA stand:** when the apply run reports `code_ready`, the orchestrator adds
  `preview` to the PR and moves the item to Deploying to QA. It polls the preview's in-cluster
  service for `/version.json`, which every image of the product serves with the commit it was
  built from; when that commit is the pushed one, it moves the item to Code review and posts the
  link. No Argo CD API and no extra RBAC are involved. After 15 minutes it moves on anyway and says
  why, with a link to the CI run. CI gating comes later; an image that is not built yet just leaves
  the pod pulling, which is what the timeout catches.
- **Previews per project:** `project.yaml` has `previews: true | false`. Off, the item goes from
  Implementing straight to Code review; that is how the loop runs before group 9 adds previews.
- **The preview id** is the tracker's project identifier and work item number, lower case
  (`ewa-12`), taken from the branch name the orchestrator chose.
- **Repository access:** the bootstrap adds an Argo CD credential template for each
  organization's prefix (`http://gitea-http.gitea.svc:3000/platform/` and `…/sdlc/`), so every
  repository is readable without a per-repo secret. Its account needs read access to every
  repository (Gitea answers `404` otherwise), so it belongs to a team of each organization that
  covers all of its repositories.
- **Fallback if the matrix spike fails:** one ApplicationSet per project, generated from the same
  directory by a small script; the spec's "one configuration entry" is then kept by the script.

### 12. CI: two act_runners in host mode, each with its own BuildKit in a user namespace

- **No custom runner image.** Each runner pod uses a public Ubuntu runner image (Gitea's
  `runner-images:ubuntu-26.04`: Node 24, npm, git, curl) and gets the `act_runner` binary from the
  official image through an init container. Steps run in host mode, inside the runner's own
  container, so there is no Docker daemon and no privileged Docker-in-Docker. Workflows download
  pinned `buildctl` and `crane` binaries and check their checksums. One job at a time per runner.
  `actions/checkout` clones over the runner's in-cluster instance URL, so it needs no CA.
- **Two runners in namespace `ci`:** `ci-platform`, registered on `platform/ai-native-sdlc` with the
  label `platform`, and `ci-products`, registered on the `sdlc` organization with the label
  `ubuntu-latest` (decision 3). Registration tokens come from Gitea's admin API in the bootstrap.
- **BuildKit is a sidecar of each runner, in the pod's own user namespace** (`hostUsers: false`):
  `moby/buildkit` in its regular (not rootless) mode with `--oci-worker-no-process-sandbox` and
  `SYS_ADMIN`, which holds only inside that namespace (root in the pod is an unprivileged uid on
  the host). It is not privileged and is reached on a shared socket. Separate sidecars keep the
  products' build cache away from the platform's. `buildkitd` pushes to `gitea.sdlc.internal`,
  which is the one place where a pod trusts the local CA: the public CA certificate comes from a
  ConfigMap.
- **Its own AppArmor and seccomp profiles, both enforcing** (spike 1.6). `sdlc-buildkit` is
  containerd's container profile plus `mount`, `remount` and `pivot_root`; the seccomp profile is
  Docker's default plus `add_key`, `keyctl` and `request_key` (runc creates a session keyring per
  build step; keyrings belong to the pod's user namespace) and `pivot_root` under `SYS_ADMIN`. The
  bootstrap's host phase installs both, as `/etc/apparmor.d/sdlc-buildkit` and
  `/var/lib/kubelet/seccomp/profiles/sdlc-buildkit.json`; the runner container keeps the defaults.
- **Why not rootless BuildKit:** it creates a user namespace of its own, and on this host's kernel
  a container's profile is stacked with `unconfined` (the cause of containerd#14485), which
  Ubuntu's `apparmor_restrict_unprivileged_userns` then refuses, whatever our profile allows.
  Unconfined AppArmor and seccomp, the usual advice, were rejected.
- **Pushing:** the products' CI pushes as `ci`, in a team of `sdlc` with write access to
  packages and releases, whose token is an Actions secret of the `sdlc` organization. The
  platform's CI pushes as `ci-platform`, in a team of the `platform` organization, whose token
  is an Actions secret of this repository only (decision 2). Gitea's job token is refused by the
  registry.
- **Ephemeral registrations:** each runner registers for one job (`--ephemeral`) in a loop, so
  every job starts in a wiped directory, Gitea drops finished runners, and the pod and its
  BuildKit cache stay.
- **Workflows in the product repository:** `ci.yaml` on every push: `npm ci`, tests, build, image
  `:<sha>`. `release.yaml` on push to `main`: next minor tag from the latest `v*` tag, a Gitea
  release whose notes list the `EWA-*` ids from the merge commits since the previous tag, and the
  version as a second tag on the commit's image (`crane tag`).
- A NetworkPolicy keeps `ci` away from the database, Plane and the orchestrator; a runner's only
  credentials are its registration, the `ci` push token and the workflow token Gitea gives each
  job.
- **Spike 1.6** ran the whole chain: a push, a job on the runner, a Dockerfile with a `RUN` step,
  the push to the registry, a pull by k3s without a pull secret, and `/version.json` with the
  commit, in about 25 s from push to image.

### 13. Names and certificates for environments

- **Pods:** nothing to do; the CoreDNS rewrite already matches names at any depth.
- **Host:** `dnsmasq` listens on `127.0.0.2:53` only and answers every name under
  `sdlc.internal`, at any depth, with `127.0.0.1` (`address=/sdlc.internal/127.0.0.1`). It runs
  as a systemd unit of our own from the `dnsmasq-base` package that Ubuntu already has; the
  `dnsmasq` package is not installed, since its service would take port 53. systemd-resolved
  sends only that domain to it through a DNS delegate (systemd 258+),
  `/etc/systemd/dns-delegate.d/sdlc.dns-delegate` with `DNS=127.0.0.2` and
  `Domains=~sdlc.internal`, which is not a default route; all other lookups are untouched, and
  nothing depends on the cluster running. The existing `/etc/hosts` block stays as a fallback.
  The bootstrap's host phase installs both (sudo).
- **The tilde matters (spike 1.2):** without it the domain is also a search domain, so every
  failed lookup, offline or a typo, is retried as `<name>.sdlc.internal` and answered with
  `127.0.0.1`. The spike verified the resolver with the VPN client on, off, offline and back.
- **Alternatives:** a daemon that rewrites `/etc/hosts` from the cluster's Ingresses (runs as
  root and lags behind); exposing the cluster's CoreDNS to the host (names die with the cluster,
  and answers point at ClusterIPs, the path the VPN client broke before); NetworkManager's
  dnsmasq mode (takes over the host's whole DNS).
- **Spike 1.2** confirmed that the VPN client leaves queries to `127.0.0.2` alone: the local
  routing table answers before its rules. The fallback (preview names written into `/etc/hosts`)
  is not needed.
- **Certificates:** each environment's Ingress carries `cert-manager.io/cluster-issuer: local-ca`
  and a TLS secret in its own namespace, so cert-manager issues a certificate for exactly that
  host. No per-project change on the platform side.

### 14. Plane: the agent account, project setup and webhooks

Plane CE has no documented API for creating users or webhooks, so the setup follows the admin
precedent from `platform-core`: a PostSync hook Job in Plane's API image runs a Django-shell
script that, idempotently, creates the `agent` user and its API token (stored as a Secret for the
orchestrator), the workspace and project `EWA` with the loop's states, adds `agent` and the
project's people as members, and registers the webhook to the orchestrator. The spike confirms
what the script can do; anything it cannot becomes a documented manual step.

Spike 1.3 found no manual step. The user, its API key and the webhook (`WebhookSerializer`, with
`issue`, `issue_comment` and `project`) need the Django shell; the project, its states and its
members go through Plane's public API with an admin key, as the orchestrator itself will call
it. The default states except Backlog are deleted; Backlog stays the default state, where new
tickets land before intake moves them to Drafting. Plane e-mails assignees and mentioned users,
so the Job turns the agent's e-mail notifications off.

Built in group 4: the project files reach the Job as the ConfigMap `target-projects`, generated
by `platform/projects/kustomization.yaml`, which the orchestrator can include too; the Job
writes the agent's key into `sdlc` through a Role limited to that Secret; the admin Job keeps the
instance admin's username at `admin`, which Plane would otherwise generate anew on every
rebuild, so a project file can name the admin as a person (anyone else needs a username set on
purpose); Plane's default statuses are removed only when the project is created, so statuses
people add stay. Plane limits every API key to 60 calls a minute (`API_KEY_RATE_LIMIT`, global,
not per key): the Job waits out a `429`, and the orchestrator's Plane client must too.

### 15. Releases are prepared by the orchestrator itself

`prepare_release` needs no agent run. The orchestrator compares `main` with `development` through
the Gitea API, collects ticket ids from the merge commits (`Merge pull request 'EWA-12: …'`), takes
the next minor version after the latest tag (`v0.1.0` when there is none), opens "Release 0.1.0"
with the ticket list, and replies with the link. When Gitea sends `release` published, the
orchestrator moves every listed ticket to Released and comments the version.

Built in task 10.1 (`orchestrator/src/orchestrator/releases.py`): the commits come from Gitea's
commits API (`development`, not `main`), and the tickets from the merge commits of `feat/<KEY>_…`
branches, as the product's release workflow reads them; asked again, the open release pull
request is found and its title and list brought up to date. Only an item On dev moves to
Released. A mention of `@agent` on the release pull request gets "only a person merges a release
into main" and nothing else. A pull request merged by squash or rebase leaves no merge commit, so
its ticket is missing from the release; the loop's own merges are merge commits.

### 16. Secrets and configuration

- `secrets.manifest` gains `LLM_API_KEY` (external, required: the model provider's key, RouterAI's
  for the author), `PG_ORCHESTRATOR_PASSWORD`,
  `PLANE_WEBHOOK_SECRET` and `GITEA_WEBHOOK_SECRET` (generated), and later `GITEA_ADMIN_EMAIL`
  (external: the Gitea admin's address, out of git; decision 10).
- Tokens that only make sense for one cluster (Gitea `agent`, `agent-runner`, `registry-pull`,
  the Argo CD credential template, the two runner registrations, Plane's `agent` API key) are created
  by the bootstrap or the Plane setup Job and stored only in cluster Secrets, as in `platform-core`.
- Orchestrator settings (the provider's base URL, intent and agent models, confidence threshold,
  run limits) are in `platform/apps/orchestrator/config.yaml`.

## Risks / Trade-offs

- [The plan is about 30–45 hours of work, and a demo is wanted within days] → Tasks are ordered so
  the loop from ticket to merge into `development` works first (groups 1–8, CI included because
  it builds the orchestrator). Previews and releases follow. If the demo comes before them, the
  README shows the rest.
- [The VPN client breaks the wildcard resolver or Job egress] → Spikes 1.1 and 1.2 showed neither:
  the resolver works with the VPN client on and off and offline, and a Job reached the model
  provider through the node. The `/etc/hosts` fallback stays documented in decision 13.
- [The host's kernel stacks container profiles with `unconfined`, so rootless tools cannot
  create user namespaces in pods] → BuildKit runs in its regular mode in a pod with its own user
  namespace, under AppArmor and seccomp profiles of its own (decision 12). Anything else that
  needs a user namespace in a pod meets the same limit.
- [A cheap model writes a poor spec or code] → Both gates are human; the model is configuration,
  and the step up for coding and review is a GLM-5.3 or Kimi K3-class model, as the README plans;
  tasks on `example-webapp` are deliberately small.
- [Plane cannot do in a script what decision 14 needs] → The spike decides; manual steps are
  documented and checked by a smoke check, so a rebuild cannot silently miss them.
- [The ApplicationSet matrix with Gitea's PR generator misbehaves] → Spike; fallback in
  decision 11.
- [CI runs agent-written code in the runner's container] → The products' runner never builds
  platform images; own BuildKit, own namespace policy, no platform secrets, no host socket, one
  job at a time, and the pod's own user namespace, so root in it is not root on the host. The worst case is a poisoned image of the product, which a person still reviews
  before `main`.
- [A broken commit on `main` stops the orchestrator's build] → The new pod never becomes ready,
  the old one keeps running, and the failed run is visible in Gitea; the next good commit fixes
  it.
- [Argo CD syncs before the image exists] → The pod retries the pull and starts when CI has
  pushed it (D19).
- [Ubuntu's kernels from 7.0.0-42 stack containerd's AppArmor profile
  (`kernel.apparmor_restrict_unprivileged_unconfined=1`), so container processes run as
  `cri-containerd.apparmor.d//&unconfined` and may no longer signal each other: OpenCode
  core-dumps, Postgres latches and Go preemption are denied (containerd#14485, fix in the open
  PR #14487, no k3s release has it)] → The bootstrap installs containerd's own profile with the two
  stacked-peer rules from that PR into `/etc/apparmor.d/`, where it loads before k3s and
  containerd keeps it; AppArmor stays enforcing and nothing else is allowed. A smoke check shows
  whether it is still needed, and it is removed once the pinned k3s has the fix. The
  alternatives were weaker: the sysctl set to 0 relaxes the whole host, and `Unconfined` per pod
  drops AppArmor for that pod.
- [A model misreads a comment] → The policy table and Gitea's protections bound the damage to a
  wrong but permitted step in `development`, which the next comment can undo.
- [This change edits a second repository] → The `example-webapp` tasks are a separate group, and
  its commits are reviewed and made there like this repository's.

## Migration Plan

New components only; nothing that runs today changes behavior, except `bootstrap.sh` gaining
steps (the AppArmor and seccomp profiles, registries file, agent and CI accounts, runner
registrations, the Argo CD credential template, resolver) that check before they act. Target projects
are seeded by `seed-projects.sh` after the bootstrap.
Rollout follows the task groups; each group ends with its smoke checks. Rollback is reverting the
group's commits: Argo CD prunes the removed apps, and `platform/README.md` lists how to undo the
host changes (`registries.yaml`, the resolver), as it already does for `platform-core`.

## Open Questions

- Whether intent reading should use a cheaper model than the agent runs. It is configuration and
  can be tuned after the first runs.
- How long to keep finished Jobs and their ConfigMaps for debugging; one hour to start.
