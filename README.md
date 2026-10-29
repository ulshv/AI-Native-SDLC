# AI-native SDLC

> From ticket to deployed change: AI agents do the work, humans make the decisions.

![status](https://img.shields.io/badge/status-design%20phase-f59e0b)
![python](https://img.shields.io/badge/python-LangGraph%20%C2%B7%20deepagents-3776ab)
![platform](https://img.shields.io/badge/platform-k3s%20%C2%B7%20Argo%20CD-0f766e)
![specs](https://img.shields.io/badge/specs-OpenSpec-7c3aed)
&nbsp;·&nbsp; **English** | [Русский](README_ru.md)

**Ship changes at the speed of a ticket.**
An AI-native software development pipeline for companies going all-in on AI: a bug report
or feature request becomes a reviewed spec, a tested PR and a deployed change. Agents
drive it, people gate it. Built to slot into enterprise SDLC workflows on a self-hosted
stack.

> [!NOTE]
> **Status: design phase.** Everything below is the target architecture. It isn't deployed
> to the cloud yet, and development happens on a local machine. Progress is tracked in the
> [roadmap](#roadmap), and every decision with its trade-offs is in
> [docs/decisions.md](docs/decisions.md).

---

## The loop

![The AI-native delivery loop](docs/diagrams/loop.svg)

1. **Signal**: a human files a ticket, or an anomaly in logs or traces opens one automatically.
2. **Triage**: an agent reads the related logs (Loki) and LLM traces (Langfuse) and works out what is actually wrong.
3. **Delta spec**: the agent proposes the fix or feature as an [OpenSpec](https://github.com/Fission-AI/OpenSpec) change, a reviewable *delta* against the current specs, opened as a pull request.
4. **Spec approval** *(human)*: a developer or product owner approves, requests changes, or answers the agent's clarifying questions from Plane, Gitea or Mattermost.
5. **Implementation**: the approved spec is handed to a coding agent that runs as an isolated Kubernetes Job and opens a code PR.
6. **Code review** *(human)*: the regular PR review. The bot cannot merge.
7. **Deploy**: merge to the environment branch, and Argo CD syncs the cluster.
8. **Observe**: logs and traces feed the next signal.

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart LR
    signal([Signal<br/>logs · traces · human]) --> ticket[Ticket<br/>Plane]
    ticket --> triage[Triage agent<br/>Loki + Langfuse]
    triage --> spec[Delta spec<br/>OpenSpec change → spec PR]
    spec --> gate1{{Spec approval<br/>human}}
    gate1 -- clarify / request changes --> spec
    gate1 -- approve --> impl[Implementation<br/>OpenCode in k8s Job]
    impl --> gate2{{Code review<br/>human}}
    gate2 -- changes requested --> impl
    gate2 -- merge --> deploy[Deploy<br/>Argo CD]
    deploy -- observe --> signal
```

</details>

## Architecture

![Architecture](docs/diagrams/architecture.svg)

The system has three layers:

| Layer | What it is | Who builds it |
|---|---|---|
| **Target app** | A business AI agent (LangGraph / deepagents) that the loop maintains. It emits logs and Langfuse traces. | us |
| **SDLC control plane** | The orchestrator, channel adapters, guardrails and the executor that launches agent jobs. | us |
| **Platform** | Tracker, git and CI, chat, observability, GitOps, identity, all on k3s. | off-the-shelf, configured as code |

Two parts of the control plane are kept deliberately separate:

- **Orchestrator** (long-lived, ours): a LangGraph graph per work item with a Postgres
  checkpointer. It can wait days for a human. Human gates are `interrupt()` and resume.
- **Harness** (short-lived, swappable): the coding agent that does the actual work in a
  Kubernetes Job. It starts as **OpenCode with free models**, behind an `Executor`
  interface, so it can later be replaced by a custom LangGraph harness without touching
  the orchestrator.

<details>
<summary>Mermaid source</summary>

```mermaid
flowchart LR
    subgraph people[People]
        po([Product owner])
        dev([Engineer])
    end
    subgraph surfaces[Collaboration surfaces]
        plane[Plane CE<br/>tickets]
        gitea[Gitea<br/>spec & code PRs]
        mm[Mattermost<br/>summaries · quick approvals]
    end
    subgraph cp[SDLC control plane]
        adapters[Channel adapters] --> norm[Event normalizer]
        norm --> orch[Orchestrator<br/>LangGraph · Postgres checkpointer]
        orch --> guard[Guardrails]
        guard --> exec[Executor]
    end
    subgraph runtime[Execution & runtime]
        job[k8s Job<br/>OpenCode harness]
        app[Target app<br/>deepagents]
        obs[Loki + Grafana<br/>Langfuse]
    end
    po & dev <--> plane & gitea & mm
    plane & gitea & mm <-- webhooks / API --> adapters
    exec --> job
    job -- branch + PR --> gitea
    app --> obs
    obs -- context for triage --> orch
    argo[Argo CD] -- deploys --> app
```

</details>

## Work item lifecycle

![Work item lifecycle](docs/diagrams/lifecycle.svg)

The orchestrator owns the state of every work item. Plane, Gitea and Mattermost are
**adapters**: each one turns its webhooks into a common event
(`approve | request_changes | reject | answer`) and mirrors state back. That
makes the following rules easy to enforce:

- **One source of truth.** A Plane status, a Gitea review or a Mattermost button all reach the same transition.
- **No echo loops.** Events written by the bot itself are dropped. Otherwise, mirroring a status to Plane would trigger another transition.
- **No races.** Every event carries the spec revision (the PR head SHA) it refers to. A stale approval is ignored, and its author gets a notification.
- **Correlation.** One record links the Plane issue, the OpenSpec change, the spec PR, the code PR, the Mattermost thread and the LangGraph thread.
- **Resumable clarifications.** When the agent is unsure, it asks in the ticket and the chat thread, then waits. An answer from either place resumes it.

<details>
<summary>Mermaid source</summary>

```mermaid
stateDiagram-v2
    direction LR
    [*] --> TicketOpen
    TicketOpen --> Triaged: triage agent
    Triaged --> Drafting
    Drafting --> AwaitingClarification: needs info
    AwaitingClarification --> Drafting: answer
    Drafting --> SpecReview: spec PR opened
    SpecReview --> Drafting: request changes
    SpecReview --> Closed: reject
    SpecReview --> SpecApproved: approve
    SpecApproved --> Implementing: dispatch k8s Job
    Implementing --> PRReview: code PR opened
    PRReview --> Implementing: changes requested
    PRReview --> Merged: human merge
    Merged --> Deployed: Argo CD sync
    Deployed --> Done: archive change · close ticket
    Closed --> [*]
    Done --> [*]
```

</details>

## Stack

| Concern | Choice | Why | Considered |
|---|---|---|---|
| Target app and orchestrator | **Python, LangGraph, deepagents** | Python-first agent ecosystem. `interrupt()` and checkpointers map directly onto human gates. | — |
| Coding harness | **OpenCode** + free models | Already wired to OpenSpec commands. Swappable behind an `Executor` interface. | custom LangGraph harness (later) |
| Specs | **OpenSpec** | Delta specs are a reviewable contract between humans and agents. | plain prompts / tickets |
| Git and CI | **Gitea** + Gitea Actions | ~200 MB RAM. Required approvals, approver allowlists and dismissal of stale approvals are free. GitHub Actions compatible. | GitLab CE (4–8 GB RAM, no required approvals in the free tier), Forgejo, OneDev |
| Tracker | **Plane CE** | Jira-like features, REST API and webhooks in the free edition. | GitLab Issues, OpenProject, Redmine, Taiga |
| Chat | **Mattermost TE** | Interactive message buttons for quick approvals. | — |
| Logs | **Loki + Grafana** | Lightweight, and LogQL is easy for an agent to query. | OpenSearch |
| LLM traces | **Langfuse** (self-hosted) | Traces both the target app and the SDLC agents themselves. | — |
| Deploy | **Argo CD** | GitOps: the cluster is reconciled from git, including the platform itself. | Flux |
| Cluster | **k3s** on the host | Real Kubernetes with no VM overhead on a 32 GB laptop. | minikube, kind |
| Database | **One Postgres via CloudNativePG** | One database per service in a single cluster, to save memory. | a Postgres per chart |
| Identity | **Keycloak** | OIDC for Gitea, Grafana, Argo CD and Langfuse. Plane CE and Mattermost TE use local accounts, because OIDC is paid there. | — |

## Security model

The riskiest input in the system is **text the agent didn't write**: logs and traces
contain end-user input, and auto-created tickets carry it straight into an agent that
writes code. The defense has two layers:

| Layer | Controls |
|---|---|
| **Orchestrator** (content) | prompt-injection classifiers, PII detection and redaction, provenance tags on untrusted text |
| **Platform** (blast radius) | per-Job ServiceAccount with minimal RBAC · a token scoped to one repository · NetworkPolicy egress limited to Gitea and the LLM API · no production secrets in agent Jobs · branch protection, so the bot cannot merge |

Above both sit the two human gates: no spec is implemented and no code is merged without
a person approving it.

## Platform on one laptop

The whole platform targets a single 32 GB machine. Components arrive **just in time**:
each one lands with the first change that needs it, not up front.

| Component | Est. RAM |
|---|---|
| k3s, Traefik, Argo CD | ~1.4 GB |
| Postgres (CNPG, shared) | ~0.8 GB |
| Gitea and its runner | ~0.8–2.3 GB |
| Plane CE | ~2.5 GB |
| Mattermost, Keycloak | ~1.4 GB |
| Langfuse (with ClickHouse) | ~3 GB |
| Loki, Grafana, Alloy | ~0.8 GB |
| Orchestrator, target app, agent Job | ~1.5 GB |
| **Total** | **~12–14 GB** *(estimate)* |

LLMs are remote, and running models locally on top of this would not fit.

## Roadmap

Each step is one OpenSpec change: spec first, then implementation.

- [ ] **0. Platform core**: k3s, Argo CD app-of-apps, CloudNativePG, Gitea and its runner, Plane CE
- [ ] **1. Spec loop (Plane only)**: ticket → spec PR → clarify / approve via ticket status
- [ ] **2. More channels**: Mattermost buttons and threads, Gitea PR reviews
- [ ] **3. Implementation phase**: approved spec → OpenCode k8s Job → code PR → human merge
- [ ] **4. Target app**: a deepagents business agent with Langfuse tracing
- [ ] **5. Triage**: logs (Loki) and traces (Langfuse) in the spec agent's context
- [ ] **6. Auto-tickets**: anomalies open tickets on their own
- [ ] **7. Continuous deploy and SSO**: Argo CD per-environment sync, Keycloak

Once step 1 works, changes to the orchestrator itself go through the orchestrator.

## How we'll know it works

| Metric | Why it matters |
|---|---|
| Lead time, ticket → merged PR | the point of the whole exercise |
| Spec first-pass approval rate | how well the agent understands intent |
| Clarification rounds per item | how much human attention the loop costs |
| Code PR rework rate | harness quality and model quality |
| Cost and tokens per change | whether free models are enough (from Langfuse) |

## Repository

```
openspec/        specs (source of truth) and changes in flight
docs/            decisions and diagrams
platform/        Argo CD apps and Helm values          (planned)
orchestrator/    the SDLC control plane                (planned)
apps/            target business agent                 (planned)
```

This repository is built the way it describes: every change starts as an OpenSpec
proposal with delta specs and is implemented only after review.

---

Decisions and trade-offs: **[docs/decisions.md](docs/decisions.md)**
