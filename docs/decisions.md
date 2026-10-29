# Decisions

A short log of architecture decisions and the reasoning behind them. Each entry gives the
context, the decision, and what it costs. Status: **accepted** unless noted.

[← back to README](../README.md)

---

### D1. The tracker is chosen, not built

**Context.** The project started as "build a task tracker". The real goal is an
AI-native delivery process, and the tracker is just one of its inputs.
**Decision.** Use an off-the-shelf, self-hosted tracker and spend the effort on the loop.
**Consequence.** We integrate through APIs and webhooks, and the tracker's data model is a given.

### D2. Plane CE as the tracker

**Context.** GitLab and Gitea issues lack what an enterprise tracker usually offers (boards
with workflows, cycles, modules, intake).
**Decision.** Plane Community Edition (AGPL-3.0), which includes a REST API and webhooks.
**Consequence.** OIDC/SAML login is a paid Plane feature, so Plane uses local accounts
(see D9).

### D3. Gitea instead of GitLab CE

**Context.** GitLab CE idles at roughly 4–8 GB RAM. In its free tier, MR approvals can't be
made mandatory.
**Decision.** Gitea with Gitea Actions. It idles at about 200 MB, and branch protection
covers required approvals, approver allowlists, dismissing stale approvals and blocking on
rejected reviews.
**Consequence.** Less "enterprise-realistic" than GitLab. Git access sits behind an adapter,
so switching later stays local. Forgejo was the close runner-up. Gitea was picked for
familiarity and its official Helm chart and MCP server.

### D4. The orchestrator and the harness are separate layers

**Context.** A ticket can wait days for a human, while a coding run takes minutes.
**Decision.** The orchestrator (ours, long-lived) is a LangGraph graph per work item with a
Postgres checkpointer. Human gates are `interrupt()` and resume. The harness
(short-lived) sits behind an `Executor` interface.
**Consequence.** The harness can start as OpenCode with free models and later be swapped for
a custom LangGraph harness without touching workflow logic.

### D5. The harness runs as a Kubernetes Job, not a CI job

**Context.** Running the agent inside CI mixes concerns, and the orchestrator would then
learn results by polling.
**Decision.** The orchestrator creates a k8s Job per agent run. CI (Gitea Actions) only
lints, tests and builds the resulting PRs.
**Consequence.** The orchestrator gets Job status directly, and RBAC, Secrets and
NetworkPolicy become guardrails at the infrastructure level. We own the Job launcher.

### D6. Specs live in git, and chat is a surface

**Context.** Approvals and clarifications must be possible from Plane, Gitea or Mattermost.
**Decision.** The delta spec is a PR in Gitea, so the audit trail stays in git. Every
channel is an adapter that emits normalized events (`approve | request_changes | reject |
answer`). The orchestrator owns the state.
**Consequence.** The adapters must drop events authored by the bot (to prevent echo loops),
reject events for a stale spec revision (to prevent races), and resolve identities across
systems.

### D7. Guardrails in two layers

**Context.** Logs, traces and auto-created tickets contain untrusted text that reaches an
agent with write access to code.
**Decision.** Content-level guardrails in the orchestrator (injection classifiers, PII
handling, provenance tags), plus blast-radius limits on the platform (repo-scoped tokens,
minimal RBAC, egress NetworkPolicy, no production secrets, a bot that cannot merge).
**Consequence.** Classifiers are probabilistic, which is why the platform layer must not
rely on them.

### D8. k3s on the host, platform managed by GitOps

**Context.** One 32 GB laptop. Docker Desktop's VM alone held 8 GB.
**Decision.** k3s directly on Ubuntu, Argo CD app-of-apps, and one shared Postgres through
CloudNativePG. Components arrive just in time, with the change that first needs them.
**Consequence.** Estimated ~12–14 GB for the full platform. Running LLMs locally is out of
scope, and models are remote. Bootstrap is a chicken-and-egg problem: Argo CD starts from an
external or local repo and moves to Gitea once Gitea is up.

### D9. Keycloak where SSO is free

**Context.** Generic OIDC is a paid feature in both Plane CE and Mattermost TE. A
"Keycloak pretends to be GitLab" workaround exists for Mattermost but is fragile.
**Decision.** Keycloak OIDC for Gitea, Grafana, Argo CD and Langfuse. Local accounts with
matching emails in Plane and Mattermost. The orchestrator maps identities by email.
**Consequence.** Users log in more than once. SSO is not on the critical path of the loop.

### D10. Thin platform first, then the loop

**Context.** Building the full platform first risks weeks of yak-shaving. Building the loop
on docker-compose first means throwaway work and integration surprises later.
**Decision.** Step 0 deploys only what step 1 needs (k3s, Argo CD, Postgres, Gitea, Plane).
Step 1 is the spec loop with Plane as the only channel.
**Consequence.** Mattermost, Loki, Langfuse and Keycloak arrive later. The spec loop can
improve itself once it runs.

---

### Open

- **Monorepo or separate platform repo.** We lean towards a monorepo (`platform/` next to
  `orchestrator/`, one OpenSpec root). A separate repo would mirror enterprise ownership
  boundaries more closely.
- **Free model viability.** Long OpenSpec workflows may exceed what free models handle
  reliably. Langfuse traces of harness runs will decide.
