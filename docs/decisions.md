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
scope, and models are remote. Bootstrap is a chicken-and-egg problem, because Argo CD needs a
git server that it deploys itself. D12 solves it.

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

### D11. One repository for platform, orchestrator and apps

**Context.** A separate platform repository would mirror enterprise ownership boundaries more
closely.
**Decision.** A monorepo: `platform/`, `orchestrator/` and `apps/` side by side under one
OpenSpec root.
**Consequence.** One history, and specs for infrastructure and for the loop live next to each
other. Splitting later is a `git filter-repo` away.

### D12. Bootstrap from the local clone, then Argo CD adopts

**Context.** Argo CD needs a git server, but the git server (Gitea) runs inside the cluster
that Argo CD manages. Rebuilding the cluster would destroy the repository with it.
**Decision.** The local clone is the seed. An idempotent `platform/bootstrap.sh` installs only
k3s, Postgres, Gitea and Argo CD, pushes the clone into Gitea, and points Argo CD at it. Argo CD
then adopts those components, using the same Helm values files and release names.
**Consequence.** A rebuild needs only the clone and the secrets directory, with no external
service involved. The script and Argo CD must render identical manifests, or adoption causes
churn. Application data is not restored (backups are a separate change). Specified in
change `platform-core`.

### D13. `sdlc.internal`, resolved twice, and a CA that outlives the cluster

**Context.** Service names must work the same from the host browser and from pods, offline and
across Wi-Fi changes. `.local` is taken by mDNS on Ubuntu.
**Decision.** Zone `sdlc.internal`. The host maps names to `127.0.0.1` in a managed
`/etc/hosts` block, and pods get a CoreDNS rewrite to Traefik's Service. TLS starts on day one:
a local root CA kept outside the cluster, plus one wildcard certificate as Traefik's default.
**Consequence.** The host trusts the CA once, and rebuilds don't break that trust. Adding a
service is one line in `platform/hosts.txt`. CA trust inside pods is deferred to Keycloak.

### D14. Secrets live outside the repository; no Vault for now

**Context.** `main` may be mirrored publicly, and coding agents read the working tree. The
platform needs database passwords, session keys and a few API tokens, and the values must
survive rebuilds.
**Decision.** Values live in `~/.config/ai-native-sdlc/secrets.env` (outside the working
tree). The repository holds only a manifest of secret names. The bootstrap generates missing
values and applies them as plain Kubernetes Secrets. SOPS, Sealed Secrets and Vault/OpenBao
were rejected for now.
**Consequence.** Nothing secret can leak through git or reach an agent's context. The secrets
directory is the one thing to back up, for example in a password manager. Vault/OpenBao stays
the candidate if agent Jobs need short-lived, audited credentials.

### D15. Gitea becomes `origin`; GitHub is an optional mirror of `main`

**Context.** The GitHub repository was created as a public showcase. The loop's pull requests,
reviews and branch protection belong on the self-hosted git server.
**Decision.** The bootstrap moves `origin` to Gitea and keeps the old remote as `github`.
`main` reaches GitHub only by a manual `git push github main`, through a pre-push hook that
refuses every other ref and any commit containing a value from `secrets.env`. Gitea's built-in
push mirror is not used, because it mirrors every ref. A Gitea Actions workflow was rejected:
it needs a runner and a GitHub credential in Gitea, and it could scan only by pattern, without
access to the secret values.
**Consequence.** Agent branches never become public. GitHub is as fresh as the last manual
push. Removing the `github` remote stops publishing, and the GitHub repository can be removed
without affecting anything else.

### D16. On the laptop, the node gets a fixed address of its own

**Context.** D13 keeps service names working offline and across networks, but the control plane
had the same weakness underneath. k3s takes the Wi-Fi address as the node's, and the Kubernetes
API Service points at it. Offline the address disappears, on another network it changes, and
the operators and Argo CD lose the API. This showed up in the offline test.
**Decision.** The node gets `10.254.0.1` on a dummy interface `sdlc0`, set as `node-ip` and
`flannel-iface` in k3s's `config.yaml`. A drop-in for the k3s unit creates the interface on
every start, next to the routing rule for VPN clients.
**Consequence.** The cluster works the same offline, on any Wi-Fi and with a VPN on. This is for
the laptop only. A cloud or multi-node host uses its own stable address as the node's and
creates no such interface, because on several nodes it would give every node the same address.
Nothing under `platform/apps/` refers to it, so moving to the cloud touches only the bootstrap.
Specified in change `platform-core`.

---

### Open

- **Free model viability.** Long OpenSpec workflows may exceed what free models handle
  reliably. Langfuse traces of harness runs will decide.
