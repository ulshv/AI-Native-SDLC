# Proposal

> **TL;DR**
> - **What:** a self-hosted platform core on the laptop: k3s, Argo CD, Postgres, Gitea and Plane, all managed from `platform/` in this repo.
> - **How you use it:** one command, `platform/bootstrap.sh`, builds or rebuilds everything. After that, every change is a git push.
> - **Why now:** every later roadmap step (the spec loop, agent Jobs, triage) needs a git server and a tracker to talk to.
> - **Side effect:** `origin` moves from GitHub to Gitea. GitHub can stay as a read-only mirror of `main`, or be dropped.
> - **Not included:** chat, observability, SSO, the orchestrator and backups. Each arrives with the change that needs it.

## Why

Every later step of the roadmap (the spec loop, the implementation phase, triage) needs a
running platform to talk to: a git server, a tracker, a database and a GitOps controller.
Nothing exists yet, and the platform must be reproducible on a single 32 GB laptop from the
repository itself. Without that, every experiment with the loop would start with hand-made,
unrepeatable infrastructure.

## What Changes

- Add a `platform/` directory to this repository with everything needed to stand up the
  platform core on k3s: Argo CD apps, pinned Helm chart values, and cluster manifests.
- Add an idempotent `platform/bootstrap.sh`. It is the only imperative step: it installs
  k3s, seeds secrets from outside the repository, installs the minimum needed to host git
  (Postgres, Gitea), pushes the local clone into Gitea, and hands control to Argo CD.
- After bootstrap, all platform state is reconciled from git by Argo CD, including the
  components the script installed and Argo CD itself.
- Platform services are reachable from the host and from pods under one name per service
  in the `sdlc.internal` zone, over HTTPS with certificates from a local CA that survives
  cluster rebuilds.
- One shared Postgres cluster serves all platform services, with a separate database and
  credentials for each.
- Secrets live outside the repository, are generated on first run where possible, and never
  enter git, so the public mirror cannot leak them.
- Gitea becomes the primary git remote for this repository. An optional read-only public
  mirror receives the `main` branch only.
- Plane CE runs as the issue tracker.
- Out of scope: Mattermost, Loki/Grafana, Langfuse, Keycloak/SSO, the orchestrator, data
  backups, multi-node and cloud deployment. Each arrives with the change that first needs it.

## Capabilities

### New Capabilities

- `platform-bootstrap`: creating or rebuilding the platform from the local clone and the
  out-of-repo secrets directory with a single idempotent command, and handing over to GitOps.
- `gitops-reconciliation`: the cluster's platform state is declared in git and continuously
  reconciled from it, in dependency order, with no manual `kubectl apply` after bootstrap.
- `platform-ingress`: stable service names in the `sdlc.internal` zone that resolve the same
  way from the host and from pods, and HTTPS on every name with a locally trusted CA.
- `platform-secrets`: where secret values come from, how they are generated, persisted and
  rotated, and the guarantee that they never enter git.
- `platform-database`: a shared Postgres that gives each platform service its own database
  and credentials.
- `source-hosting`: Gitea as the primary git remote, and the optional public mirror limited
  to `main`.
- `issue-tracking`: Plane CE available as the tracker, backed by the shared database.

### Modified Capabilities

_None. There are no existing specs yet._

## Impact

- **Repository:** new `platform/` tree, a `secrets.env.example` contract, and `.gitignore`
  rules for secret material. `docs/decisions.md` gains entries for bootstrap, DNS/TLS and
  secrets.
- **Host (requires sudo):** k3s installed as a system service. A managed block in
  `/etc/hosts`. The local CA added to the system trust store and to the browser NSS store.
  A secrets directory at `~/.config/ai-native-sdlc/`.
- **Resources:** about 5–6 GB RAM for the platform core. Stopping Docker Desktop's VM is
  recommended but not required.
- **Git workflow:** `origin` moves from GitHub to Gitea once bootstrap succeeds. GitHub
  becomes an optional read-only mirror of `main`.
- **Dependencies:** pinned versions of k3s, Argo CD, cert-manager, CloudNativePG, Traefik,
  Gitea and Plane CE Helm charts.
