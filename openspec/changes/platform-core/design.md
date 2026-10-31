# Design

> **TL;DR**
> - **Bootstrap installs the minimum, then hands over:** k3s, Postgres, Gitea and Argo CD by script. The local clone is pushed into Gitea, and Argo CD adopts everything (decision 2).
> - **One name per service, two resolvers:** `*.sdlc.internal` goes to `127.0.0.1` on the host via `/etc/hosts`, and to Traefik in pods via a CoreDNS rewrite. Both work offline (decision 4).
> - **TLS on day one:** one wildcard certificate at Traefik, signed by a local CA that lives outside the cluster and so survives rebuilds (decision 5).
> - **Secrets outside the repo:** values in `~/.config/ai-native-sdlc/secrets.env`, generated on first run. The repo holds only a manifest of names (decision 6).
> - **`origin` moves to Gitea:** a workflow mirrors only `main` to GitHub (decision 9).
> - **Biggest risk:** Plane may refuse webhooks to private addresses. It is checked first with a spike.

## Context

See `proposal.md` for the motivation and `specs/` for the required behavior.

- **Starting point:** no infrastructure. One Ubuntu laptop (32 GB RAM, 12 cores), currently
  running Docker Desktop's VM. The repository's `origin` is a public GitHub repository that
  was created as a showcase.
- **Constraints:** single node; everything must fit next to a desktop session; free and
  self-hosted components only. The repository may be mirrored publicly, so nothing secret can
  live in it. The operator is strong in software architecture and new to hands-on Kubernetes
  operations, so the setup should stay conventional and debuggable.

## Goals / Non-Goals

**Goals:**
- One imperative entry point (`platform/bootstrap.sh`). Everything after it is GitOps.
- Rebuilding from scratch takes one command and needs only the local clone and
  `~/.config/ai-native-sdlc/`.
- A conventional layout that later changes (Mattermost, observability, Keycloak, the
  orchestrator) extend by adding one directory under `platform/apps/`.

**Non-Goals:**
- High availability, multi-node, or anything cloud-specific.
- Backups of application data. A rebuild intentionally starts with empty tickets and reviews.
- CA trust *inside* pods. It arrives with Keycloak, which is the first in-cluster client that
  must validate platform certificates.
- NetworkPolicies and per-workload RBAC hardening. These arrive with the agent Jobs.
- Git over SSH. Machines (Argo CD, agent Jobs, the mirror) use scoped HTTPS tokens anyway.
  Humans push over HTTPS too, through the credential helper from decision 9, which makes it
  as seamless as SSH. SSH would need a separate port, a host key persisted across rebuilds,
  and key registration. It can be added later as a separate change if the HTTPS flow proves
  inconvenient.

## Decisions

### 1. k3s directly on the host, bundled Traefik disabled

k3s is installed as a system service with `--disable traefik`. Its ServiceLB (klipper) and
local-path storage stay enabled. Traefik is installed by Argo CD like any other component,
because the bundled one is managed by k3s's own HelmChart controller and would sit outside
GitOps.

- **Alternatives:** minikube or kind (a VM or Docker layer adds overhead and moves networking
  further from production setups); k3d (disposable, but runs inside Docker Desktop's VM).

### 2. Bootstrap: install the minimum, push, then let Argo CD adopt

`bootstrap.sh` runs these phases in order. Each phase checks the current state first, which
is what makes the script idempotent:

```
 0. preflight      sudo available; tools present; ports 80/443 free; required external secrets present
 1. secrets        generate missing values and the root CA -> ~/.config/ai-native-sdlc/
                   (local files only, so the CA exists before phase 2 trusts it)
 2. host           k3s; /etc/hosts block; CA into system + browser trust stores; apply k8s Secrets
 3. git hosting    CloudNativePG operator + Postgres cluster + Gitea   (helm, values from platform/apps/)
 4. repository     create org/repo; move origin; push branches and tags   (see decision 9)
                   (via a temporary port-forward to Gitea's Service, because ingress and TLS
                    only arrive with Argo CD in phases 5-6)
 5. gitops         Argo CD (helm) + root Application pointing at Gitea
 6. adoption       wait until Argo CD reports every Application Synced/Healthy;
                   delete the Helm release records of phase 3/5 (resources stay)
```

Phases 3 and 5 read their Helm values from the same `platform/apps/<name>/values.yaml` files
that Argo CD uses, and use the same release names and namespaces. When Argo CD reconciles,
the rendered manifests are identical, so it takes ownership without restarting anything.
Phase 6 deletes the `sh.helm.release.v1.*` Secrets so that Helm no longer believes it owns
the releases. Running `helm uninstall` instead would delete the resources themselves.

On a cluster where Argo CD already manages these components, phases 3 and 5 detect the
existing Applications and skip them. This satisfies the hand-over requirement.

- **Alternative:** bootstrap Argo CD from the public GitHub repository. That is simpler, but a
  rebuild would then depend on an external service that may be removed.
- **Alternative:** Terraform. It has no real providers for the host steps (k3s, `/etc/hosts`,
  CA trust), which would become `local-exec` shell anyway. Helm releases created by Terraform
  would get two owners once Argo CD adopts them. And its state file keeps secret values in
  plain text. Ansible fits the host steps better, but is not worth a Python toolchain for
  about 150 lines of check-then-act shell. Revisit Ansible for several hosts, and Terraform
  for a cloud move; the GitOps layer stays the same either way. Nomad is not an alternative
  here: it would replace Kubernetes, not the bootstrap.

### 3. Argo CD: app-of-apps, sync waves, automated sync

`platform/argocd/root.yaml` is an Application that points at `platform/apps/` and renders
one child Application per component. Every child uses automated sync with `prune` and
`selfHeal`, and sets `ServerSideApply=true`, which large CRDs such as cert-manager's need.

| Wave | Components |
|---|---|
| -2 | cert-manager, CloudNativePG operator (CRDs and operators) |
| -1 | CA ClusterIssuer and wildcard Certificate, Traefik with its TLSStore, `coredns-custom`, Postgres cluster and databases |
| 0 | Gitea and its runner, Plane CE, Argo CD itself |

Argo CD fetches the repository over Gitea's in-cluster service URL
(`http://gitea-http.gitea.svc:3000/...`), not through the ingress. It therefore needs no CA
trust and does not depend on DNS rewriting. Its read token is created by the bootstrap
through the Gitea API.

- **Alternative:** an ApplicationSet with a git directory generator. It is less explicit about
  per-app sync options and waves, so we start with plain Applications.

### 4. Names: `/etc/hosts` on the host, a CoreDNS rewrite in the cluster

- **Host:** `bootstrap.sh` keeps a block between `# BEGIN ai-native-sdlc` and
  `# END ai-native-sdlc` in `/etc/hosts`, mapping every name in `platform/hosts.txt` to
  `127.0.0.1`. ServiceLB publishes Traefik's ports 80 and 443 on the host.
- **Pods:** a `coredns-custom` ConfigMap in `kube-system`, which k3s's CoreDNS loads by design.
  It adds a single rule that rewrites `*.sdlc.internal` to Traefik's Service
  (`traefik.traefik.svc.cluster.local`, answer rewritten automatically).
- Both paths end at the same Traefik and the same certificate, and neither depends on the
  host's network address. This is what the offline and network-switch scenarios require.
- **VPN clients on the host.** A VPN in TUN mode (sing-box, Clash, WireGuard, Tailscale) adds
  policy routing rules that can capture pod traffic: sing-box sends all port 53 traffic into
  its tunnel, so pods' DNS queries never reach CoreDNS once the Service IP is translated. The
  bootstrap adds `ip rule add to 10.42.0.0/16 lookup main priority 100`, ahead of every such
  client, and a drop-in for the k3s unit re-adds it whenever k3s starts. Pods still reach the
  internet through the VPN.
- **Zone:** `sdlc.internal`. ICANN reserves `.internal` for private use. `.local` belongs to
  mDNS on Ubuntu, and some software treats `.localhost` specially.
- **Alternatives:** dnsmasq wildcard (it conflicts with systemd-resolved, and there are only
  about ten names); `sslip.io` (depends on external DNS, gives ugly URLs, and `127.0.0.1` is
  wrong inside pods); the LAN IP (it changes between networks).

### 5. TLS: a persistent local CA, one wildcard certificate at the edge

- The CA key pair lives in `~/.config/ai-native-sdlc/ca.{key,crt}`. If it is missing, the
  bootstrap generates it with openssl and loads it as the Secret `cert-manager/root-ca`. A
  CA `ClusterIssuer` uses it. The CA carries a name constraint for `sdlc.internal`, so the
  trusted key cannot sign certificates for any other domain.
- **The root CA is valid for 1 year.** Each bootstrap run checks its expiry. When fewer than 30
  days remain, the run generates a new CA, installs host trust for it, removes host trust for
  the old one, and updates the Secret, after which cert-manager re-issues the wildcard
  certificate. The leaf certificate itself is renewed by cert-manager automatically.
- One `Certificate` for `*.sdlc.internal` is issued in the `traefik` namespace and set as
  Traefik's default `TLSStore` certificate. Application charts configure plain HTTP ingress
  routes and never handle TLS. Traefik's `web` entrypoint redirects to `websecure`.
- **Host trust:** the CA is copied to `/usr/local/share/ca-certificates/` and installed with
  `update-ca-certificates`. It is added with `certutil` to `~/.pki/nssdb`, which Chrome on
  Linux uses. Firefox on Linux ignores the system store, and `security.enterprise_roots` works
  only on Windows and macOS, so Firefox gets a `Certificates.Install` policy in
  `/etc/firefox/policies/` that points at a copy of the CA there; the Firefox snap can read
  that directory. Firefox imports the CA into its profile, so after a rotation it keeps
  trusting the old CA until it expires (at most 30 days, and only for `sdlc.internal`).
- **Alternative:** cert-manager generates a self-signed CA. It would be lost on every rebuild,
  and the host would have to re-trust it each time.

### 6. Secrets: a file outside the repo, a manifest inside it

- **`platform/secrets.manifest`** (tracked) has two kinds of lines. A `value` line names a
  secret, its kind (`password`, `key` or `external`), whether it is required, and what it is
  for.
  A `put` line says where a value goes in the cluster: namespace, Secret, key, and a template
  in which `${NAME}` is replaced by that value. Templates exist because charts want more
  than one bare value per key: one value often goes to several Secrets (a role password for
  both CloudNativePG and Gitea), next to non-secret constants (`username`), or inside a
  connection string (Plane's `DATABASE_URL`). The manifest holds names and templates only,
  never values. This is the contract the spec calls "the list of required secret names".
  `platform/secrets.env.example` is derived from it for humans.
- **`~/.config/ai-native-sdlc/secrets.env`** (mode 0600) holds the values. When a `password`
  or `key` value is missing, the bootstrap generates it and appends it. When a `required`
  `external` value is missing, preflight fails.
- **Generated values are random base58** (letters and digits without `0 O I l`), so they are
  safe inside URLs and connection strings. A `password` is only hashed or compared and gets
  22 characters (128 bits). A `key` is turned into an encryption or signing key by the app
  (Gitea and Plane hash their `SECRET_KEY` into an AES key) and gets 44 characters (256 bits).
  A raw key with a fixed format, such as 64 hex characters, gets its own kind when the first
  one appears.
- **Secrets are applied** with `kubectl apply --server-side` from a list rendered out of the
  manifest, so re-running is safe and rotation is just editing the file and re-running.
  Server-side apply keeps no `last-applied` annotation, so values are not copied into metadata.
  The Secrets are not in git, and charts reference them through `existingSecret`-style
  values. Argo CD ignores objects it does not own.
- **`.gitignore`** gets `secrets.env`, `*.key` and `.secrets/` as a second line of defense.
- **No pre-commit hook.** A leak into the local, private Gitea is recoverable. The only path
  to public exposure is the mirror, so the scan sits there instead (decision 9). A hook would
  also protect only the operator's clone, because agent Jobs work in fresh clones without it.
- **In this change:** Gitea admin password, Gitea secret key and internal token, the Postgres
  role passwords for `gitea` and `plane`, Plane's `SECRET_KEY` and `LIVE_SERVER_SECRET_KEY`,
  Plane's MinIO and RabbitMQ passwords and `PLANE_ADMIN_PASSWORD` are generated; the three
  secret keys are of kind `key`, the rest `password`.
  `PLANE_ADMIN_EMAIL` is `external` and required. `GITHUB_MIRROR_TOKEN` is `external` and
  required only when the mirror is enabled. Tokens that only make sense for one cluster (the Gitea runner
  registration token, and the Argo CD and git-push tokens) are created by the bootstrap
  through the Gitea API on every rebuild, not persisted.
- **Alternatives:** SOPS with age (secrets in git, but needs a KSOPS plugin in Argo CD);
  Sealed Secrets (the controller's key must be backed up off-cluster); Vault or OpenBao
  (unseal on every reboot, much more machinery, and its benefits are short-lived audited
  credentials, which nothing needs yet).

### 7. Database: one CloudNativePG cluster, one database and role per service

- Namespace `databases`, Cluster `platform-pg`, one instance, local-path PVC.
- **Roles** are declared through `managed.roles` with `passwordSecret` pointing at the seeded
  Secrets. Because the passwords come from `secrets.env`, they stay the same across rebuilds.
- **Databases** `gitea` and `plane` are declared with CNPG's `Database` resources, each owned
  by its role.
- **Isolation needs one extra step.** Postgres grants `CONNECT` to `PUBLIC` on every new
  database, so without it any role can open any database. The Cluster's `pg_hba` rules close
  that: each service role may connect only to its own database, and a final
  `host all all all reject` refuses every other network connection before the password is
  checked. This is what makes the spec's "connection is refused" scenario true. The operator
  is not affected, because its own connections use the local socket and the certificate rule
  for replication, which come before user rules. A new service adds one line next to its
  `Database`.
- **Alternative:** a SQL Job that runs `REVOKE CONNECT ... FROM PUBLIC` after the databases
  exist. It needs a psql image, retries and hook ordering to wait for the operator, where
  `pg_hba` is three declarative lines that hold from the cluster's first start.

### 8. Gitea and its Actions runner

- **Chart dependencies** `postgresql-ha` and `valkey-cluster` are disabled. The database is
  `platform-pg/gitea`, and cache, session and queue use Gitea's built-in backends.
- `ROOT_URL=https://gitea.sdlc.internal/`. SSH is disabled in Gitea; the chart still creates
  an empty `gitea-ssh` Service, which nothing listens behind. Data lives on a local-path PVC,
  and the Deployment uses `Recreate`, because a second pod could not share the volume or the
  LevelDB queue. Self-registration is off: the admin creates accounts.
- **Secret values** reach Gitea through `additionalConfigFromEnvs` from the seeded
  `gitea-secrets` Secret. The chart writes them into `app.ini` on the first start and never
  overwrites `SECRET_KEY` or `INTERNAL_TOKEN` afterwards, which matches the rule that those
  are not rotated. The database password is re-applied on every start.
- **`act_runner`** is deployed by Argo CD in host mode (jobs run as processes in the runner
  pod, with no Docker-in-Docker). That is enough for the mirror job. Container-based CI
  arrives with the change that needs it.

### 9. Moving `origin` to Gitea and mirroring `main`

Phase 4 of the bootstrap:

1. Creates the org `sdlc` and the private repository `sdlc/ai-native-sdlc` through the Gitea
   API, if they are missing.
2. If `origin` does not already point at `https://gitea.sdlc.internal/sdlc/ai-native-sdlc.git`:
   - renames `origin` to `github` (and fails if a `github` remote already exists with another URL);
   - adds the Gitea URL as `origin`;
   - pushes all branches and tags. The first push and the API calls go through a temporary
     `kubectl port-forward` to Gitea's Service, because the public name only works after
     Traefik and the certificate are reconciled in phases 5-6;
   - sets `main` to track `origin/main`. Plain `git push` to the public URL works from the
     end of the bootstrap onwards.
3. Configures a git credential helper scoped to `https://gitea.sdlc.internal` that reads the
   push token from the secrets directory, so no token is written into `.git/config` or
   `~/.git-credentials`.

**Mirror:** `.gitea/workflows/mirror-main.yml` runs on pushes to `main` and force-pushes
`HEAD:main` to `github.com/ulshv/AI-Native-SDLC` with `GITHUB_MIRROR_TOKEN`. The bootstrap
stores that token as a repository Actions secret. Force-push makes the mirror strictly
one-way. Enabling or disabling the mirror means adding or deleting this file.

**Leak gate:** on a push to any branch, the workflow scans that branch's history with a pinned
gitleaks release, so a leak shows up before it reaches `main`. On `main`, the force-push runs
only after a clean scan. If anything is found, the job fails and the mirror is not updated. This is
the one gate on the way to public exposure, and it catches every path into `main`: human
commits, agent commits and `--no-verify`. It works by pattern only: the runner deliberately
has no access to `secrets.env`. The exact-value check therefore stays in the local smoke
check (task 3.4). Deleting the mirror workflow removes the gate together with the exposure it
guards.

- **Alternative:** Gitea's built-in push mirror. It mirrors every ref, so agent branches would
  become public.

### 10. Plane CE

- Plane's official Helm chart (`plane-ce`), with its bundled Postgres disabled and the
  external database set to `platform-pg/plane`. Redis, RabbitMQ and MinIO stay with the chart
  for now.
- The ingress host is `plane.sdlc.internal` with no TLS settings, because the edge provides TLS.
- **The instance admin is created automatically**, by an Argo CD PostSync hook Job that runs
  inside Plane's API image. The email comes from `PLANE_ADMIN_EMAIL` (external and required,
  kept in `secrets.env` so a personal address stays out of the public repo). The password is
  `PLANE_ADMIN_PASSWORD` (generated). The Job is idempotent: if the admin already exists, it
  does nothing.
- **The mechanism needs a spike.** Plane documents no Helm value or environment variable for
  this, and the official flow is the `/god-mode/` wizard. A community Railway template
  automates it with its own `ADMIN_EMAIL` wrapper. The most likely route is a management
  command in Plane's backend. Automation also closes the window in which anyone who reaches
  `/god-mode/` first could claim the instance. **Fallback:** if no supported mechanism
  exists, the wizard stays a documented one-time step and the corresponding requirement in
  `issue-tracking` is revised.

### 11. Layout and namespaces

```
platform/
  bootstrap.sh            the only imperative entry point
  README.md               how to run, rebuild and fully remove the platform
  hosts.txt               service names in sdlc.internal
  secrets.manifest        secret contract (names, kinds, targets)
  secrets.env.example     generated from the manifest, for humans
  argocd/root.yaml        app-of-apps
  apps/<component>/       app.yaml + values.yaml + extra manifests
.gitea/workflows/mirror-main.yml
```

There is one namespace per component: `argocd`, `cert-manager`, `cloudnative-pg`, `databases`,
`traefik`, `gitea`, `plane`.

## Risks / Trade-offs

- **[Adoption drift]** If the bootstrap's Helm install and Argo CD's render differ, Argo CD
  "fixes" live resources and may restart Gitea mid-push. → Both read the same values files.
  Phase 6 waits for Synced/Healthy before declaring success.
- **[Argo CD manages itself]** A bad change to Argo CD's own values can break the controller
  that would revert it. → A documented recovery: `helm upgrade` with the values from the
  last good commit. This is a manual procedure, not part of the bootstrap.
- **[Plane webhooks to private addresses]** Plane may refuse webhook URLs that resolve to
  private IPs (SSRF protection), which would break the in-cluster webhook requirement. →
  This is the first Plane task: a spike that verifies delivery to an in-cluster endpoint
  before anything else is built on it. If it fails, we look for a setting that allows the
  internal range before changing the approach.
- **[Plane admin automation]** No documented mechanism exists. → A spike at the start of the
  Plane task, with the manual wizard as a fallback (decision 10).
- **[ServiceLB on loopback]** `127.0.0.1:443` reaching Traefik relies on how klipper publishes
  host ports. → It is verified early in the Traefik task. The fallback is running Traefik
  with `hostNetwork` on ports 80 and 443.
- **[Port conflicts]** Docker Desktop or other local services may hold 80 or 443. → Preflight
  checks the ports and names the process.
- **[Memory]** Plane's chart brings Redis, RabbitMQ and MinIO. → Every component has
  explicit resource requests and limits in its values. We measure after bootstrap and update
  the README estimate.
- **[Rebuild loses data]** Tickets and review threads are lost on rebuild. → Accepted for
  this change and stated in the spec. Backups get their own change.
- **[Public mirror exposure]** Everything on `main` becomes public, including workflow files.
  → No secret values in the repository (by spec), and the token lives only in Gitea's
  Actions secrets.

## Migration Plan

1. Implement the change in the local clone. The current `origin` is still GitHub. Merge it
   into the local `main` before the first full run, because Argo CD's root Application tracks
   `main` and must find `platform/` there.
2. Run `platform/bootstrap.sh`. Phase 4 moves `origin` to Gitea and keeps GitHub as `github`.
3. Verify the spec scenarios, the smoke checks from `tasks.md`.
4. If the mirror is wanted, put `GITHUB_MIRROR_TOKEN` into `secrets.env`, re-run the
   bootstrap and push to `main`. Otherwise delete the mirror workflow, and optionally the
   `github` remote and the GitHub repository.

**Rollback:** k3s's own `/usr/local/bin/k3s-uninstall.sh` removes the cluster and all its
data. The `/etc/hosts` block and the host CA trust are harmless leftovers, and
`platform/README.md` documents the two commands that remove them. There is no separate
teardown script, because it would only wrap these steps.
`git remote remove origin && git remote rename github origin` restores the old remote. The
secrets directory is never deleted automatically.

## Open Questions

- **Exact chart and k3s versions:** pinned to the latest stable releases at implementation
  time.
- **Keeping the GitHub mirror long-term:** the design supports both outcomes without changes.
