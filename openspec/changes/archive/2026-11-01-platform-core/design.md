# Design

> **TL;DR**
> - **Bootstrap installs the minimum, then hands over:** k3s, Postgres, Gitea and Argo CD by script. The local clone is pushed into Gitea, and Argo CD adopts everything (decision 2).
> - **One name per service, two resolvers:** `*.sdlc.internal` goes to `127.0.0.1` on the host via `/etc/hosts`, and to Traefik in pods via a CoreDNS rewrite. Both work offline (decision 4).
> - **TLS on day one:** one wildcard certificate at Traefik, signed by a local CA that lives outside the cluster and so survives rebuilds (decision 5).
> - **Secrets outside the repo:** values in `~/.config/ai-native-sdlc/secrets.env`, generated on first run. The repo holds only a manifest of names (decision 6).
> - **`origin` moves to Gitea:** `main` reaches GitHub only by a manual push, which a pre-push hook checks for secret values (decision 9).
> - **Biggest risk:** Plane may refuse webhooks to private addresses. A spike confirmed it does, and that `WEBHOOK_ALLOWED_IPS` allows the cluster's networks (decision 10).

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
- Git over SSH. Machines (Argo CD, agent Jobs) use scoped HTTPS tokens anyway.
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

`platform/argocd/root.yaml` is an Application that renders one child Application per
component from `platform/apps/*/app.yaml`, and renders itself too, so a change to the root
(such as its list of not-yet-configured components) also arrives through git. Every child
uses automated sync with `prune` and `selfHeal`, and sets `ServerSideApply=true`, which large
CRDs such as cert-manager's need. Argo CD compares server-side, so fields that admission
webhooks default do not show as drift, and polls git every minute.

| Wave | Components |
|---|---|
| -2 | cert-manager, CloudNativePG operator (CRDs and operators) |
| -1 | CA ClusterIssuer and wildcard Certificate, Traefik with its TLSStore, `coredns-custom`, Postgres cluster and databases |
| 0 | Gitea, Plane CE, Argo CD itself |

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
- **The control plane does not depend on it either.** k3s would take the Wi-Fi address as the
  node's, and the Kubernetes API Service points at the node's address: offline it disappears,
  on another network it changes, and the operators and Argo CD lose the API (found in the
  offline test). The node therefore gets `10.254.0.1` on a dummy interface `sdlc0`
  (`node-ip` and `flannel-iface` in k3s's `config.yaml`), created by the k3s drop-in below on
  every start. This is for the laptop only: a cloud or multi-node host uses its own stable
  address as the node's and creates no such interface (on several nodes it would give every
  node the same address). Nothing under `platform/apps/` refers to it.
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
  to public exposure is publishing `main` to GitHub, so the check sits on that push instead
  (decision 9). Agent Jobs cannot publish at all: they have no GitHub credentials.
- **In this change:** Gitea admin password, Gitea secret key and internal token, the Postgres
  role passwords for `gitea` and `plane`, Plane's `SECRET_KEY` and `LIVE_SERVER_SECRET_KEY`,
  Plane's MinIO and RabbitMQ passwords and `PLANE_ADMIN_PASSWORD` are generated; the three
  secret keys are of kind `key`, the rest `password`.
  `PLANE_ADMIN_EMAIL` is `external` and required. Tokens that only make sense for one
  cluster (the Argo CD and git-push tokens) are created by the bootstrap through the Gitea API and replaced after a rebuild, so they are not values in
  `secrets.env`. The read token lives only in Argo CD's repository Secret; the push token is
  a file next to `secrets.env`, because the git credential helper reads it from there.
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

### 8. Gitea

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
- Gitea Actions and its runner arrive with the change that first needs CI.

### 9. Moving `origin` to Gitea and publishing `main`

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

**Publishing to GitHub** is a manual `git push github main` from the operator's clone.
Nothing pushes there automatically, and nothing flows back: GitHub is never fetched into
`main`. Removing the `github` remote stops publishing altogether.

**Pre-push hook.** The bootstrap points `core.hooksPath` at `platform/hooks/`. For a push to
any `github.com` remote, `platform/hooks/pre-push`:

- refuses every ref except `main`, and every tag, so work-in-progress branches never leave
  the laptop;
- refuses the push when a pushed commit contains a value from `secrets.env`, naming the file
  but not the value. This exact-value check catches what pattern scanners miss, such as a
  random password or the admin email;
- runs gitleaks on the pushed commits when it is installed, for well-known token formats.

Pushes to Gitea are not checked: a leak there stays private and recoverable. `git push
--no-verify` skips the hook, which is the operator's explicit choice.

- **Alternatives:** a Gitea Actions workflow that scans with gitleaks and force-pushes `main`
  on every push. It needs a runner, a GitHub credential stored in Gitea and an image with
  ssh, and its scan works by pattern only, because the runner has no access to `secrets.env`.
  Gitea's built-in push mirror publishes every ref, so agent branches would become public.

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
- **The mechanism** (found by a spike): Plane documents no Helm value or environment variable
  for this, and its `create_instance_admin` command only promotes an existing user and leaves
  the wizard open. The Job therefore runs Django's shell and posts to the wizard's own sign-up
  endpoint in-process (Django's test client, so no network and no CSRF). The admin is set up
  exactly as the wizard does it, `/god-mode/` stops offering the setup, and nobody who reaches
  it first can claim the instance.
- **Webhooks to in-cluster services** need `WEBHOOK_ALLOWED_IPS`: Plane refuses private
  addresses by default and checks again at delivery. It is set to the pod and service
  networks (`10.42.0.0/16,10.43.0.0/16`). The chart has no value for extra environment, so this
  and `CELERY_WORKER_CONCURRENCY=2` ride in the seeded `plane-app-env` Secret; uncapped,
  Celery starts one process per CPU core and ran out of memory.
- **Argo CD renders the chart through kustomize** (`helmCharts` in
  `apps/plane/kustomization.yaml`, enabled by `kustomize.buildOptions: --enable-helm` in
  `argocd-cm`). The chart stamps every pod template with the render time and names its Jobs
  after the Helm revision, which Argo CD never increments, so each push would restart Plane and
  fail on the immutable Jobs; `ignoreDifferences` cannot stop it, because Argo CD applies the
  new stamp anyway. Kustomize removes the stamp and turns the chart's Jobs into sync hooks.
- Images are pulled `IfNotPresent` (the chart defaults to `Always`, which fails offline), and
  the containers the chart renders without resources get a namespace `LimitRange`.

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
  hooks/pre-push          checks what is pushed to GitHub
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
- **[Plane webhooks to private addresses]** Plane refuses webhook URLs that resolve to
  private IPs (SSRF protection). → Resolved by the spike: `WEBHOOK_ALLOWED_IPS` allows the
  cluster's networks only (decision 10).
- **[Plane admin automation]** No documented mechanism exists. → Resolved by the spike: the
  Job drives the wizard's own endpoint (decision 10).
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
- **[Public mirror exposure]** Everything on `main` becomes public once it is pushed to
  GitHub. → No secret values in the repository (by spec), and the pre-push hook refuses a push
  that contains one.

## Migration Plan

1. Implement the change in the local clone. The current `origin` is still GitHub. Merge it
   into the local `main` before the first full run, because Argo CD's root Application tracks
   `main` and must find `platform/` there.
2. Run `platform/bootstrap.sh`. Phase 4 moves `origin` to Gitea and keeps GitHub as `github`.
3. Verify the spec scenarios, the smoke checks from `tasks.md`.
4. Publish with `git push github main` whenever wanted. To stop publishing, remove the
   `github` remote, and optionally the GitHub repository.

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
