# Proposal

> **TL;DR**
> - **What:** the full delivery loop on one product: a Plane ticket assigned to `@agent` becomes a spec, then code in the same PR, then a preview, `dev` and a release, with people approving and merging.
> - **How you use it:** you talk to `@agent` in Plane comments and Gitea PRs; a model reads the request, code checks who may do what, and Gitea makes sure only people merge into `main`.
> - **The first product:** `example-webapp`, a small React app in its own repository and Plane project `EWA`.
> - **Why now:** the main metric and the main risk (cheap models on real OpenSpec work) only show end to end, and the whole loop is the first thing worth demonstrating.
> - **Not included:** Mattermost, gating previews on CI, stale-approval checks, Langfuse, triage and SSO.

## Why

The platform core runs, but nothing on it delivers a change yet: a ticket in Plane goes nowhere
unless a person writes the spec, the code and the deployment by hand. Roadmap step 1 is the
whole loop on one simple product, because the main metric (lead time from ticket to merged PR)
and the main risk (whether cheap models handle real OpenSpec workflows) only show up end to
end. It is also the first state of the project worth demonstrating: a live run from ticket to
release, rather than a description of one.

## What Changes

- **The orchestrator** (`orchestrator/`, Python, LangGraph): one long-lived service whose work
  happens in LangGraph threads. It takes a Plane ticket assigned to `@agent`, links the ticket
  and later its PR to one thread, reads only comments that mention it, and moves the work one
  step per event from the phase saved in the thread. Phases are mirrored to Plane statuses. The
  core does not depend on Plane or Gitea; they are its first adapters.
- **`@agent` commands:** a model turns a comment into an intent (`approve`, `request_changes`,
  `reject`, `answer`, `merge`, `archive`, `prepare_release`); a policy in code decides whether the person may do
  that in the current phase. Explicit signals (a Plane status, a Gitea review) meet the same
  policy without the model. Comments work in Plane and in Gitea PRs.
- **Agent Jobs:** each agent run is a Kubernetes Job with OpenCode and any OpenAI-compatible model
  provider (the author uses RouterAI with DeepSeek V4 Flash; both are configuration). One Job writes the OpenSpec change
  (`/opsx:propose`), another implements it (`/opsx:apply`) in the same PR, and after the code is
  approved a last commit archives the change into the project's specs (`openspec archive`). Jobs
  push with an account that cannot merge.
- **Clarifications:** when the agent lacks information it asks in the ticket and stops; the
  next comment that mentions it continues the work.
- **`example-webapp`**, the first target project: a small React app (dummy login, screens on
  mocked data) in its own Gitea repository, tracked as Plane project `EWA`. Its local clone
  lives at `~/Projects/sdlc/example-webapp` (configurable), and a new `platform/seed-projects.sh`,
  separate from the platform's bootstrap, pushes it into Gitea when missing, the way the
  bootstrap does with this repository.
- **Branches and environments:** `feat/EWA-12_<slug>` → `development` → `main`. A preview per
  PR at `ewa-12.qa.example-webapp.sdlc.internal`, started when the apply Job finishes (the
  ticket waits in Deploying to QA until it serves the new commit, then goes to Code review) and
  removed when the PR closes; `dev.example-webapp.sdlc.internal` from `development`;
  `example-webapp.sdlc.internal` from `main`. The image tag is the commit SHA everywhere.
- **Merge rules enforced by Gitea:** the orchestrator's account merges into `development` only
  when an authorized person asks; only people merge into `main`, after a human approval.
- **Releases:** on request the agent opens a release PR from `development` into `main` with
  draft notes; after a person merges it, CI tags the next minor version (`v0.1.0` for the first
  release), and the tickets in it become Released.
- **Platform additions:** a container registry in Gitea that the cluster pulls from; two Gitea
  Actions runners, one for target projects and one, limited to this repository, that builds the
  platform's own images on every push to `main` so the orchestrator deploys through GitOps like a
  product; names at any depth under `sdlc.internal` on the host through a local `dnsmasq`;
  certificates issued per environment; a database for the orchestrator; Gitea and Plane service
  accounts; and the model provider's API key as an external secret.
- **Docs:** README and decisions updated to what was built. The README example keeps its
  Mattermost step, which arrives with roadmap step 4.
- **Out of scope:** Mattermost (step 4), gating previews on a green CI, rejecting stale
  approvals by SHA, Langfuse, Loki and triage, SSO, and any target project other than
  `example-webapp`.

## Capabilities

### New Capabilities

- `work-item-orchestration`: the lifecycle of a work item from assignment to release: intake,
  phases, clarifications, the two human gates and the QA stand between them, mirroring to the
  tracker, threads linked to tickets and PRs, independence from any one tracker, and surviving
  restarts and duplicate events.
- `agent-commands`: how people instruct `@agent` from the tracker and the git server, how a
  request becomes an intent, and which requests are allowed for which person in which phase.
- `agent-execution`: how agent runs execute: isolated Jobs, the models they use, the
  credentials they get and do not get, their results and their limits.
- `target-projects`: connecting a product to the loop: its own repository and tracker project,
  seeding that repository from a local clone, its branches and protections, its OpenSpec setup,
  and `example-webapp` as the first one.
- `delivery-environments`: preview, dev and production environments per target project, what
  deploys to each and when, and image tags.
- `releases`: preparing, approving and publishing a release, and its version number and notes.
- `continuous-integration`: building and testing every push of a target project, building and
  rolling out the platform's own images from this repository, and keeping the two apart.

### Modified Capabilities

- `source-hosting`: the git server also stores container images that the cluster pulls.
- `issue-tracking`: the tracker also notifies the platform about comments and assignee
  changes, and has a service account for `@agent`.
- `platform-ingress`: names created on the fly under a target project's zone resolve and get
  trusted certificates without editing the declared list.

## Impact

- **Repository:** new `orchestrator/` (service code and tests) and `agent-runner/` (the Job
  image); `.gitea/workflows/images.yaml` that builds both; one directory per target project under
  `platform/projects/`, starting with `example-webapp/project.yaml` for `EWA`; new apps under
  `platform/apps/` that serve every project (`orchestrator`, `agents`, `ci`, and `environments`,
  whose ApplicationSets read `platform/projects/`); `platform/seed-projects.sh`; bootstrap steps
  for the registry, the agent accounts, the runners and the resolver; `platform/secrets.manifest`
  gains `LLM_API_KEY` (external).
- **A second repository:** `~/Projects/sdlc/example-webapp` with the app, its OpenSpec specs,
  its Helm chart and CI workflows. It sits outside this repository, so applying this change
  writes there too.
- **Host (requires sudo):** `/etc/rancher/k3s/registries.yaml` for the Gitea registry;
  `dnsmasq` listening on `127.0.0.2` only, plus a systemd-resolved drop-in that sends just
  `sdlc.internal` to it, if the spike confirms the VPN client leaves it alone (the `/etc/hosts`
  block stays as a fallback).
- **Resources:** about 2–4 GB more RAM while a Job and a CI build run (orchestrator, two runners
  with BuildKit, agent Job, a few small web servers).
- **External:** calls to the model provider with the operator's key and spending limit (RouterAI
  for the author); Job egress to it.
- **Dependencies:** pinned versions of LangGraph and its Postgres checkpointer, LangChain's
  OpenAI and DeepSeek chat clients, OpenCode, the OpenSpec CLI, Gitea act_runner and its runner
  image, BuildKit, crane, and Node.js for the target project.
