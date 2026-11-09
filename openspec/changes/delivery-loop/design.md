# Design

> **TL;DR**
> - **Orchestrator, threads first:** the LangGraph thread is the main entity (Agent Protocol style API); tracker work items, PRs and chat threads are only linked to a thread (`thread_id, kind, ref`), and the work's phase lives in the thread's state, one thread per work item. Webhooks go into a queue table in Postgres behind an interface (NATS when several services consume events); each event runs its thread's graph one step, with idempotent side effects and no `interrupt()` (decisions 4, 6).
> - **Words in, rules decide:** one model call, through a client chosen by the model's prefix, turns a comment into an intent; a policy table over role and phase decides, and Gitea's branch protection has the final word on merges (decisions 5, 8).
> - **Agent runs are Jobs:** OpenCode with any OpenAI-compatible provider in a fresh clone, pushing as `agent-runner`, which cannot merge; the result comes back as a termination message (decision 7).
> - **A project is one directory:** `platform/projects/<name>/project.yaml` feeds both the orchestrator and Argo CD's ApplicationSets for dev, prod and previews (decisions 10, 11).
> - **Every image is built by CI and tagged with its commit:** the platform's on a runner limited to this repository, so the orchestrator deploys through GitOps like a product; the products' on their own runner; both push to Gitea's registry, which k3s pulls directly (decisions 2, 3, 12).
> - **Biggest risk:** the time (about 30–45 hours) and the VPN client's effect on wildcard DNS; the loop up to the merge into `development` comes first, and the spikes run before anything is built.

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
- Tracing (Langfuse), Mattermost, gating previews on CI, stale-approval checks, SSO.
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
~/Projects/sdlc/example-webapp   the product, a separate repository (decision 9)
```

### 2. Registry: Gitea's container registry, pulled by k3s directly

Images live at `gitea.sdlc.internal/sdlc/<name>:<tag>`, in Gitea's built-in package registry
under the `sdlc` organization. The bootstrap's host phase writes `/etc/rancher/k3s/registries.yaml`
with the local CA and a read-only pull token for `gitea.sdlc.internal`, and restarts k3s only when
the file changed. containerd on the host resolves the name through `/etc/hosts` to Traefik, so
pulls work offline, and no namespace needs an `imagePullSecret`.

- **Alternatives:** a separate `registry:2` (one more component with its own auth and storage);
  pushing to Gitea's in-cluster HTTP service (the image name would differ from the pull name, and
  Gitea's token realm redirects to `https://gitea.sdlc.internal` anyway).

### 3. Every image is built by CI and tagged with its commit, the platform's on its own runner

The orchestrator will change often, so it is built and deployed like a product. On every push to
`main` of this repository, `.gitea/workflows/images.yaml` builds `orchestrator` and `agent-runner`
and pushes both as `:<commit>`. The orchestrator's Application passes `$ARGOCD_APP_REVISION` to
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
- **The queue is a table, behind an interface.** Recording an event, moving the phase and saving
  the graph's checkpoint happen in one database and commit together (the transactional inbox
  pattern, as in Oban, graphile-worker or pgmq). Duplicates are caught by the unique delivery id
  for as long as the row exists, while Gitea can redeliver a webhook days later.
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
  Jobs on start and enqueues results it has not recorded.
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
  The model and threshold are configuration.
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
- **Gitea:** repository webhooks for `pull_request` (closed, merged), `pull_request_review`
  (approved, rejected, comment), `issue_comment` on PRs and `release` (published). The
  orchestrator registers them for every project on start, idempotently by URL. Replies go to the
  PR. Mentions are the literal `@agent`.
- **Echo:** an event whose actor is the agent's Plane user or Gitea's `agent` or `agent-runner`
  is recorded and dropped.
- **Signatures:** Plane's `X-Plane-Signature` and Gitea's `X-Gitea-Signature` are HMAC-SHA256
  over the body with generated secrets; verified in the hardening group, after the loop works.

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

The directory leaves room for more per project without new conventions: for example
`values-dev.yaml`, `values-prod.yaml` or `values-preview.yaml` that the ApplicationSets pass to the
product's chart as a second source with `ignoreMissingValueFiles`, so the platform can override
an environment without a commit in the product's repository.

### 11. Environments with ApplicationSets

- **dev and prod:** one ApplicationSet, git files generator over `platform/projects/*/project.yaml`
  crossed with a list `[dev → development, prod → main]`. Each Application deploys the project's
  `deploy/chart` at that branch, with `image.tag=$ARGOCD_APP_REVISION` (D19), into
  `<project>-<env>`.
- **Previews:** one ApplicationSet with a matrix of the git files generator and the
  `pullRequest` generator for Gitea (in-cluster API URL, a token, `labels: [preview]`,
  `requeueAfterSeconds: 60`). With `goTemplate`, the preview id is
  `regexFind "[A-Z]+-[0-9]+" .branch | lower`, the host is `<id>.qa.<domain>`, the image tag is
  `.head_sha`, and the namespace is `<project>-<id>`. Closing the PR drops it from the generator,
  and Argo CD deletes the Application and its resources.
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
- **Repository access:** the bootstrap adds an Argo CD credential template for the prefix
  `http://gitea-http.gitea.svc:3000/sdlc/`, so every project repository is readable without a
  per-repo secret.
- **Fallback if the matrix spike fails:** one ApplicationSet per project, generated from the same
  directory by a small script; the spec's "one configuration entry" is then kept by the script.

### 12. CI: two act_runners in host mode, each with its own rootless BuildKit

- **No custom runner image.** Each runner pod uses a public Ubuntu runner image (Gitea's
  `runner-images`, which has Node, git and curl) and gets the `act_runner` binary from the
  official image through an init container. Steps run in host mode, inside the runner's own
  container, so there is no Docker daemon and no privileged Docker-in-Docker. Workflows download
  pinned `buildctl` and `crane` binaries and check their checksums. One job at a time per runner.
- **Two runners in namespace `ci`:** `ci-platform`, registered on `sdlc/ai-native-sdlc` with the
  label `platform`, and `ci-products`, registered on the `sdlc` organization with the label
  `ubuntu-latest` (decision 3). Registration tokens come from Gitea's admin API in the bootstrap.
- **BuildKit is a sidecar of each runner:** rootless `buildkitd` with unconfined seccomp and
  AppArmor, not privileged, reached on `localhost`. Separate sidecars keep the products' build
  cache away from the platform's. `buildkitd` pushes to `gitea.sdlc.internal`, which is the one
  place where a pod trusts the local CA: the CA certificate is mounted into it.
- **Workflows in the product repository:** `ci.yaml` on every push: `npm ci`, tests, build, image
  `:<sha>`. `release.yaml` on push to `main`: next minor tag from the latest `v*` tag, a Gitea
  release whose notes list the `EWA-*` ids from the merge commits since the previous tag, and the
  version as a second tag on the commit's image (`crane tag`).
- A NetworkPolicy keeps `ci` away from the database, Plane and the orchestrator; a runner's only
  credentials are its registration and the workflow token Gitea gives each job.
- **Spike 1.6** confirms host mode on the public image, the init-container binary and the push
  from the sidecar before the rest of the CI is built.

### 13. Names and certificates for environments

- **Pods:** nothing to do; the CoreDNS rewrite already matches names at any depth.
- **Host:** `dnsmasq` listens on `127.0.0.2:53` only and answers every name under
  `sdlc.internal`, at any depth, with `127.0.0.1` (`address=/sdlc.internal/127.0.0.1`).
  systemd-resolved sends only that domain to it, through a drop-in
  `/etc/systemd/resolved.conf.d/sdlc.conf` with `DNS=127.0.0.2` and `Domains=~sdlc.internal`;
  all other lookups are untouched, and nothing depends on the cluster running. The existing
  `/etc/hosts` block stays as a fallback. The bootstrap's host phase installs both (sudo).
- **Alternatives:** a daemon that rewrites `/etc/hosts` from the cluster's Ingresses (runs as
  root and lags behind); exposing the cluster's CoreDNS to the host (names die with the cluster,
  and answers point at ClusterIPs, the path the VPN client broke before); NetworkManager's
  dnsmasq mode (takes over the host's whole DNS).
- **Spike 1.2** checks that the VPN client leaves resolved's queries to `127.0.0.2` alone. If it
  does not, the fallback is a bootstrap step that writes the current preview names into
  `/etc/hosts`, run by the operator.
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

### 15. Releases are prepared by the orchestrator itself

`prepare_release` needs no agent run. The orchestrator compares `main` with `development` through
the Gitea API, collects ticket ids from the merge commits (`Merge pull request 'EWA-12: …'`), takes
the next minor version after the latest tag (`v0.1.0` when there is none), opens "Release 0.1.0"
with the ticket list, and replies with the link. When Gitea sends `release` published, the
orchestrator moves every listed ticket to Released and comments the version.

### 16. Secrets and configuration

- `secrets.manifest` gains `LLM_API_KEY` (external, required: the model provider's key, RouterAI's
  for the author), `PG_ORCHESTRATOR_PASSWORD`,
  `PLANE_WEBHOOK_SECRET` and `GITEA_WEBHOOK_SECRET` (generated).
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
- [The VPN client breaks the wildcard resolver or Job egress] → Spike first (group 1). Fallbacks:
  preview names written to `/etc/hosts` by a bootstrap step; Job egress is ordinary HTTPS through
  the node, the same path the host already uses.
- [A cheap model writes a poor spec or code] → Both gates are human; the model is configuration,
  and the step up for coding and review is a GLM-5.3 or Kimi K3-class model, as the README plans;
  tasks on `example-webapp` are deliberately small.
- [Plane cannot do in a script what decision 14 needs] → The spike decides; manual steps are
  documented and checked by a smoke check, so a rebuild cannot silently miss them.
- [The ApplicationSet matrix with Gitea's PR generator misbehaves] → Spike; fallback in
  decision 11.
- [CI runs agent-written code in the runner's container] → The products' runner never builds
  platform images; own BuildKit, own namespace policy, no platform secrets, no host socket, one
  job at a time. The worst case is a poisoned image of the product, which a person still reviews
  before `main`.
- [A broken commit on `main` stops the orchestrator's build] → The new pod never becomes ready,
  the old one keeps running, and the failed run is visible in Gitea; the next good commit fixes
  it.
- [Argo CD syncs before the image exists] → The pod retries the pull and starts when CI has
  pushed it (D19).
- [A model misreads a comment] → The policy table and Gitea's protections bound the damage to a
  wrong but permitted step in `development`, which the next comment can undo.
- [This change edits a second repository] → The `example-webapp` tasks are a separate group, and
  its commits are reviewed and made there like this repository's.

## Migration Plan

New components only; nothing that runs today changes behavior, except `bootstrap.sh` gaining
steps (registries file, agent accounts, runner registrations, the Argo CD credential template,
resolver) that check before they act. Target projects
are seeded by `seed-projects.sh` after the bootstrap.
Rollout follows the task groups; each group ends with its smoke checks. Rollback is reverting the
group's commits: Argo CD prunes the removed apps, and `platform/README.md` lists how to undo the
host changes (`registries.yaml`, the resolver), as it already does for `platform-core`.

## Open Questions

- Whether intent reading should use a cheaper model than the agent runs. It is configuration and
  can be tuned after the first runs.
- How long to keep finished Jobs and their ConfigMaps for debugging; one hour to start.
