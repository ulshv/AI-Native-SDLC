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
**Revised.** Target apps moved to their own repositories, see D17. The platform and the
orchestrator stay here.

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

### D17. Target apps live in their own repositories

Status: **proposed** (roadmap step 1).
**Context.** D11 put target apps under `apps/` in this repository. The agent would then work
next to the platform's specs, in the repository Argo CD tracks for the platform. Companies
also rarely keep a product in one repository with its delivery tooling.
**Decision.** Each target app gets its own Gitea repository with its own `openspec/`, starting
with `example-webapp` (Plane project `EWA`). The orchestrator maps a Plane project to a
repository in its configuration.
**Consequence.** The orchestrator is multi-repository from the start, and adding a product is
a configuration entry. The agent's context is one small repository. Changes that span the
platform and an app become two changes.

### D18. Feature branches, `development` and `main`, each with an environment

Status: **proposed** (roadmap step 1).
**Context.** The loop has to fit how teams already ship: review before integration, an
integration environment, deliberate releases.
**Decision.** The agent branches `feat/EWA-12_<slug>` off `development` and opens a PR into
it. A PR whose code is in place and whose CI is green gets a preview at
`ewa-12.qa.example-webapp.sdlc.internal`, deployed by an Argo CD ApplicationSet with the
pull request generator and removed when the PR closes. `development` deploys to
`dev.example-webapp.sdlc.internal` on every merge. A person releases by merging `development`
into `main` in Gitea; CI then tags the next minor version, publishes the release, and `main`
deploys to `example-webapp.sdlc.internal`. Both long-lived branches are protected.
**Consequence.** The reviewer gets a running preview at the second gate, not only a diff. The
host name is derived from the branch name, so the orchestrator's naming is a contract with the
ApplicationSet. Previews need wildcard names on the host (see Open), and a CI runner that
executes agent-written code (`npm install` included), which must run without privileged
Docker-in-Docker and without secrets beyond a registry push token.

### D19. The image tag is the commit SHA

Status: **proposed** (roadmap step 1).
**Context.** GitOps needs the deployed image to follow from git. The usual ways are a bot
committing new tags back to git, or a separate image updater.
**Decision.** CI tags every image with the commit SHA it was built from. Argo CD passes the
revision it synced (`$ARGOCD_APP_REVISION`) to the chart as the tag. Previews use the PR's
head SHA. A release adds the version as a second tag on the same image.
**Consequence.** No write-back commits, no extra controller, and one rule for all three
environments. Argo CD can sync before CI has pushed the image; the pod then retries the pull
until the image appears.

### D20. One pull request per work item: the spec first, then the code

Status: **proposed** (roadmap step 1).
**Context.** Separate spec and code PRs need a bot that merges the spec PR, or a code PR that
depends on an unmerged one.
**Decision.** The agent opens one PR with only the OpenSpec change. Approving the ticket
starts the implementation, which pushes the code and the archived change to the same branch.
An engineer merges the PR.
**Consequence.** The bot never merges anything. The PR history shows the spec the code was
built against, and stale-approval checks compare against the head SHA at approval time.
Approval in Gitea itself can come later as another channel (roadmap step 4).

---

### Open

- **Free model viability.** Long OpenSpec workflows may exceed what free models handle
  reliably. Langfuse traces of harness runs will decide.
- **Wildcard names on the host.** Previews create host names on the fly, and the managed
  `/etc/hosts` block from D13 can't hold wildcards. The candidate is a small local resolver for
  `*.sdlc.internal` that systemd-resolved queries for that domain only, which would replace the
  `/etc/hosts` block. The VPN client on the author's machine intercepts DNS traffic, so a spike
  decides whether this works before D13 is revised.
