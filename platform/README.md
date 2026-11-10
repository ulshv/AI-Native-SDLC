# Platform

The platform core (k3s, Argo CD, Postgres, Gitea, Plane) for this repository, built and
rebuilt by one command. After the bootstrap, every change to the platform is a git push.

## Run

Install the [host prerequisites](../README.md#host-prerequisites) first, then from the
repository root:

```bash
platform/bootstrap.sh   # build or rebuild the platform; safe to re-run
platform/smoke.sh       # check that it works
```

## Preflight

Phase 0 checks everything below before the script changes anything, and lists every problem
at once:

- you run it as your own user, not as root (it calls `sudo` itself, so expect a password prompt);
- the tools from the host prerequisites are installed, and `yq` is mikefarah's v4;
- ports 80 and 443 are free (the error names the process that holds them);
- every required external secret is in `~/.config/ai-native-sdlc/secrets.env`: your Plane
  admin email and the API key of an OpenAI-compatible model provider (RouterAI, OpenRouter,
  DeepSeek, OpenAI and the like):

  ```bash
  mkdir -p ~/.config/ai-native-sdlc && chmod 700 ~/.config/ai-native-sdlc
  echo 'PLANE_ADMIN_EMAIL=you@example.com' >> ~/.config/ai-native-sdlc/secrets.env
  read -rsp 'Model provider API key: ' key && echo "LLM_API_KEY=$key" >> ~/.config/ai-native-sdlc/secrets.env; unset key
  chmod 600 ~/.config/ai-native-sdlc/secrets.env
  ```

`platform/bootstrap.sh --dry-run` runs the same checks without asking for the sudo password
and prints what each phase would do.

**Freeing ports 80 and 443.** See who holds them, then stop it:

```bash
sudo ss -ltnp 'sport = :80 or sport = :443'
systemctl --user stop docker-desktop   # if it is Docker Desktop
```

## Secrets

Everything secret lives in `~/.config/ai-native-sdlc/`, outside the working tree, so nothing
that runs in the repository (agents included) can read it, and nothing can be committed:

- `secrets.env`: one `NAME=value` per line, no quotes;
- `ca.key`, `ca.crt`: the local root CA behind every `*.sdlc.internal` certificate.

[`secrets.manifest`](secrets.manifest) is the contract: which secrets exist and where each
goes in the cluster. [`secrets.env.example`](secrets.env.example) lists the names with a
description of each.

**Generated or external.** `password` and `key` values are random base58 strings that the
bootstrap creates when they are missing, so you never type them. A `password` (22 characters,
128 bits) is only hashed or compared; a `key` (44 characters, 256 bits) is turned into an
encryption or signing key by the app. `external` values come from somewhere else, and you
put them into `secrets.env` yourself: `PLANE_ADMIN_EMAIL` and `LLM_API_KEY`. Values persist
across rebuilds, so services keep the same credentials.

**The model provider's key** (`LLM_API_KEY`) goes to the orchestrator (`sdlc/orchestrator-env`)
and to the agent runs (`sdlc-agents/llm-api-key`). Set a spending limit on it at the provider:
a run is a few calls, but a misbehaving loop would keep spending.

**Rotation.** Change the value, or delete its line to get a new generated one, re-run
`platform/bootstrap.sh`, then restart the service that uses it. Two values must not be
rotated: `GITEA_SECRET_KEY` and `PLANE_SECRET_KEY` encrypt data stored in the databases
(2FA secrets, instance settings), and a new key silently makes that data unreadable.

**Backup.** This directory and the git clone are all a rebuild needs. Keep a copy off the
laptop, for example in a password manager or on an encrypted drive:

```bash
tar -czf ai-native-sdlc-secrets.tgz -C ~/.config ai-native-sdlc
```

Restore it to `~/.config/ai-native-sdlc/` before running the bootstrap on a new machine.

## Host changes and removal

Phase 2 changes the host, and each change is checked first and skipped when already in place:

| What | Where |
|---|---|
| k3s, pinned in `bootstrap.sh`, bundled Traefik disabled | system service `k3s`, `/usr/local/bin/{k3s,kubectl}`, `/etc/rancher/`, `/var/lib/rancher/` |
| The node's own address `10.254.0.1`, which stays the same offline and on any network, so the operators and Argo CD never lose the Kubernetes API | dummy interface `sdlc0`, and k3s's flags in `/etc/rancher/k3s/config.yaml` |
| Traffic to pods bypasses VPN routing rules, so a VPN in TUN mode cannot capture pods' DNS | `ip rule` priority 100 for `10.42.0.0/16` |
| Both of the above come back on every k3s start, including after a reboot | `/etc/systemd/system/k3s.service.d/ai-native-sdlc-routing.conf` |
| Processes of one container may signal each other under AppArmor again: Ubuntu's kernels from 7.0.0-42 stack containerd's profile, which then denies it ([containerd#14485](https://github.com/containerd/containerd/issues/14485)). The file is containerd's own profile plus two rules; apparmor.service loads it at boot, before k3s, so containerd keeps it | `/etc/apparmor.d/cri-containerd.apparmor.d`, from [`apparmor/`](apparmor/cri-containerd.apparmor.d) |
| k3s pulls images from Gitea's registry as `reader` (written in phase 7, see [Container registry](#container-registry)) | `/etc/rancher/k3s/registries.yaml` |
| Cluster access for you | `~/.kube/config`; a config that was already there is kept as `~/.kube/config.before-ai-native-sdlc` |
| Service names from [`hosts.txt`](hosts.txt) → `127.0.0.1` | a block between `# BEGIN ai-native-sdlc` and `# END ai-native-sdlc` in `/etc/hosts`; the original is kept as `/etc/hosts.before-ai-native-sdlc` |
| Root CA trusted by the system (curl, git, most tools) | `/usr/local/share/ca-certificates/ai-native-sdlc.crt` |
| Root CA trusted by Chrome | `~/.pki/nssdb`, nickname `ai-native-sdlc` |
| Root CA trusted by Firefox | the `Certificates.Install` policy in `/etc/firefox/policies/policies.json`, pointing at `/etc/firefox/policies/ai-native-sdlc-ca.crt` |

The CA can only sign names under `sdlc.internal`, so even a leaked key cannot impersonate
other sites. When fewer than 30 days of its year are left, the bootstrap replaces it and moves
all of the trust above to the new one. Firefox is the exception: it imports the CA into its
profile and keeps the old one until it expires.

**Removal.** `/usr/local/bin/k3s-uninstall.sh` removes the cluster with all its data. The
host changes are harmless leftovers; these commands remove them:

```bash
# 1. the /etc/hosts block
sudo sed -i '/^# BEGIN ai-native-sdlc$/,/^# END ai-native-sdlc$/d' /etc/hosts

# 2. the CA trust (if policies.json holds nothing else, you can delete it instead)
sudo rm -f /usr/local/share/ca-certificates/ai-native-sdlc.crt /etc/firefox/policies/ai-native-sdlc-ca.crt \
  && sudo update-ca-certificates --fresh \
  && certutil -d sql:$HOME/.pki/nssdb -D -n ai-native-sdlc \
  && jq 'del(.policies.Certificates.Install[] | select(endswith("/ai-native-sdlc-ca.crt")))' /etc/firefox/policies/policies.json \
     | sudo tee /etc/firefox/policies/policies.json.new >/dev/null \
  && sudo mv /etc/firefox/policies/policies.json.new /etc/firefox/policies/policies.json

# 3. the node's interface and the routing rule for pods
sudo rm -f /etc/systemd/system/k3s.service.d/ai-native-sdlc-routing.conf \
  && sudo ip link del sdlc0 \
  && sudo ip rule del to 10.42.0.0/16 lookup main priority 100

# 4. the registry access (k3s-uninstall.sh removes it with the rest of /etc/rancher)
sudo rm -f /etc/rancher/k3s/registries.yaml

# 5. the AppArmor profile; after the reboot containerd loads its own one again
sudo rm -f /etc/apparmor.d/cri-containerd.apparmor.d && sudo reboot
```

The AppArmor profile is a workaround: remove it (step 5, and the file and its bootstrap step)
once the pinned k3s ships a containerd with the fix from
[containerd#14487](https://github.com/containerd/containerd/pull/14487). The smoke check
`container_signals` tells whether it is still needed: it fails without the file on an
affected kernel.

Firefox keeps the CA it already imported. Remove it under Settings → Privacy & Security →
Certificates → View Certificates → Authorities (`ai-native-sdlc local CA`). `~/.kube/config`
and the secrets directory stay; delete them by hand if you want them gone.

## Repository and origin

Phase 4 makes the platform's Gitea the home of this repository:

- creates the org `sdlc` and the private repository `sdlc/ai-native-sdlc`;
- renames the old `origin` (GitHub) to `github`, so nothing it references is lost, and adds
  `https://gitea.sdlc.internal/sdlc/ai-native-sdlc.git` as `origin`;
- pushes every local branch and tag, and sets `main` to track `origin/main`;
- creates the admin's Gitea token `bootstrap-push` (write access to repositories) in
  `~/.config/ai-native-sdlc/gitea-push-token`;
- creates the service accounts below, with tokens that live only where they are used.

| Account | What it is for | Its token |
|---|---|---|
| `agent` | the orchestrator: comments, pull requests, merges into `development` where a target project allows it | `sdlc/gitea-agent` (write to repositories and issues) |
| `agent-runner` | agent runs: pushes feature branches, may merge nothing | `sdlc-agents/gitea-agent-runner` (write to repositories) |
| `reader` | reads every repository and package of `sdlc`, as a member of the team `readers` | `argocd/creds-sdlc` (Argo CD's credential template for every `sdlc/` repository) and `/etc/rancher/k3s/registries.yaml` (image pulls, phase 7) |

Nobody signs in as a service account: its password is random and never stored. Every token
belongs to this cluster and is replaced after a rebuild; a re-run replaces only a token that
no longer works. Which repositories `agent` and `agent-runner` may write to, and which
branches they may merge into, is set per target project by `seed-projects.sh`.

Until the ingress is up, phase 4 reaches Gitea through a temporary port-forward. Afterwards a
plain `git push` goes to `gitea.sdlc.internal`: a credential helper in this clone's
`.git/config`, scoped to that host, reads the push token from the secrets directory when git
asks, so the token itself is never written into `.git/config` or `~/.git-credentials`.

## Container registry

Images live in Gitea's own registry as `gitea.sdlc.internal/sdlc/<name>:<tag>`, under the
organization, not under a repository. Phase 7, the last one, lets k3s pull them in any
namespace without a pull secret: it writes `/etc/rancher/k3s/registries.yaml` (mode 0600)
with `reader`'s read-only package token and the local CA, and restarts k3s, which reads the
file only on start. Pods keep running through the restart; the API is away for about half a
minute. A re-run leaves the file and k3s alone while the token works. It comes last because
the token needs Gitea, which runs only from phase 3.

Pushing needs write access to the organization's packages, which only CI's own account `ci`
gets; a repository collaborator is refused, and so is Gitea Actions' job token.

The smoke check `registry_pull` runs the newest image of `sdlc` in a pod, asking the registry
each time; while the registry is empty it only says so.

**Undo:**

```bash
sudo rm /etc/rancher/k3s/registries.yaml && sudo systemctl restart k3s
```

**Rollback** to GitHub as `origin`:

```bash
git remote remove origin && git remote rename github origin
git config --remove-section credential.https://gitea.sdlc.internal
```

## Changing the platform through git

After the bootstrap, Argo CD owns every component, itself included. The root Application
([`argocd/root.yaml`](argocd/root.yaml)) renders one Application per
`apps/<component>/app.yaml`, and itself, and keeps each in line with `main` on Gitea:

- **To change something**, edit `apps/<component>/values.yaml` (or the manifests next to it),
  or raise the pinned chart version (`targetRevision` in its `app.yaml`; for Plane, `version`
  in its `kustomization.yaml`), commit, and push to `main`. Argo CD
  notices within about three minutes. `platform/smoke.sh gitops_synced_to_main` waits until
  every Application is Synced and Healthy at the commit you pushed. Until the ingress exists,
  `platform/bootstrap.sh` does the push through a port-forward.
- **Manual edits do not stick.** Anything changed with `kubectl edit` or in k9s is reverted
  within seconds. Change git instead.
- **To add a component**, create `apps/<component>/` with an `app.yaml` (sync wave, automated
  sync) and its values or manifests.
- **Watch it** with `kubectl get applications -n argocd`, or `:applications` in k9s.

The bootstrap installed Argo CD, CloudNativePG and Gitea with Helm once, then deleted Helm's
release records, so `helm list -A` is empty on purpose. Re-running the bootstrap leaves
anything Argo CD manages alone.

The bootstrap's last step also gives up Helm's and its own claims on the fields of everything
Argo CD manages (`managedFields`), and removes what only they had set, such as Helm's own
labels. Kubernetes drops a field that is deleted from git only when no one else still claims
it; without this step, a value deleted from `values.yaml` would stay in the cluster while
Argo CD showed Synced.

**Recovering a broken Argo CD.** A bad change to `apps/argocd/values.yaml` can break the
controller that would revert it. Reinstall it from the last good commit with Helm, outside
GitOps:

```bash
good=<last good commit>
git show "$good":platform/apps/argocd/values.yaml > /tmp/argocd-values.yaml
version=$(git show "$good":platform/apps/argocd/app.yaml | yq '.spec.sources[0].targetRevision')
helm upgrade --install argocd argo-cd --repo https://argoproj.github.io/argo-helm \
  --version "$version" --namespace argocd --values /tmp/argocd-values.yaml --wait
```

Then fix or revert the bad commit on `main`, and run `platform/bootstrap.sh` once Argo CD
reports itself Synced again: its last phase deletes the Helm release record again.

## Service URLs

| Service | URL | Sign in |
|---|---|---|
| Gitea | <https://gitea.sdlc.internal> | `gitea_admin`, password `GITEA_ADMIN_PASSWORD` from `secrets.env` |
| Argo CD | <https://argocd.sdlc.internal> | `admin`, password: `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` |
| Plane | <https://plane.sdlc.internal> | `PLANE_ADMIN_EMAIL`, password `PLANE_ADMIN_PASSWORD` from `secrets.env` |

The same names work from inside pods: CoreDNS answers every `*.sdlc.internal` name with
Traefik's Service. Pods do not trust the local CA yet, so a client in a pod has to be given
`ca.crt` explicitly.

**After the CA is trusted for the first time, restart the browsers.** Chrome reads
`~/.pki/nssdb` and Firefox reads its policies only at startup. Until then they show a
certificate warning.

To add a name, append it to [`hosts.txt`](hosts.txt) and re-run `platform/bootstrap.sh`: the
host's `/etc/hosts` gets it, pods resolve it already, and the wildcard certificate covers it.

## Public mirror

GitHub (`github.com/ulshv/AI-Native-SDLC`, the `github` remote) is a public copy of `main`,
updated by hand:

```bash
git push github main
```

Nothing pushes there automatically, and nothing comes back: never pull GitHub into `main`.
Pull requests or commits made on GitHub are not merged; take a change over with
`git cherry-pick` into Gitea if it is wanted, and it reaches GitHub with the next push. To stop
publishing, remove the `github` remote.

**The pre-push hook** ([`hooks/pre-push`](hooks/pre-push), enabled by the bootstrap through
`core.hooksPath`) checks every push to a `github.com` remote and refuses it when:

- it pushes anything but `main`: another branch, a tag, or the deletion of `main`;
- a pushed commit contains a value from `secrets.env`, a password or the admin email alike.
  It names the file, never the value;
- gitleaks, when installed, finds a well-known secret format in the pushed commits. Without
  gitleaks the hook says so and checks the exact values only.

Pushes to Gitea are not checked. `git push --no-verify` skips the hook: use it only if you are
sure. The two secret checks live in [`secrets-leak-check.sh`](secrets-leak-check.sh), which
also runs by hand: with no argument it checks the whole history of `HEAD` and the working
tree, uncommitted and untracked files included; with a revision range, only those commits.

**When the hook refuses a push**, the secret is in a commit on `main`, so it is in Gitea too.
Treat it as leaked: rotate it in `secrets.env` and re-run the bootstrap (see Secrets). Then
remove it from history, for example with `git rebase -i` for a recent commit or
`git filter-repo --replace-text` for an old one, force-push the rewritten `main` to Gitea, and
push to GitHub again.

`platform/smoke.sh mirror_only_main` checks that GitHub holds `main` and nothing else.

## Plane

Plane CE, the issue tracker, runs at <https://plane.sdlc.internal> on the platform's Postgres
(database `plane`); Redis, RabbitMQ and MinIO come with its chart. Argo CD renders the chart
through [`apps/plane/kustomization.yaml`](apps/plane/kustomization.yaml), which patches two
things Helm values cannot change: a render timestamp on every pod, which would restart Plane
after every push, and the chart's Jobs, which become sync hooks.

**Admin.** Sign in with `PLANE_ADMIN_EMAIL` and `PLANE_ADMIN_PASSWORD` from `secrets.env`; the
instance settings are at <https://plane.sdlc.internal/god-mode/>. The admin is created by a
Job ([`apps/plane/admin-job.yaml`](apps/plane/admin-job.yaml)) that runs after every sync and
does nothing once an admin exists, so the first-run wizard never opens. For the same reason,
changing `PLANE_ADMIN_PASSWORD` later does not change the password: change it in Plane
(Profile settings → Security) and keep `secrets.env` in step. A rebuild creates a new admin
from `secrets.env`, because the data does not survive it.

**API key.** In Plane: Profile settings → Personal Access Tokens → Add token. A client sends it
in the `X-API-Key` header to `https://plane.sdlc.internal/api/v1/`, for example:

```bash
curl -H @<(printf 'X-API-Key: %s\n' "$PLANE_API_KEY") \
  https://plane.sdlc.internal/api/v1/workspaces/<workspace>/projects/
```

**Webhooks.** In Plane: Workspace settings → Webhooks → Add webhook, with the events to send.
A receiver in the cluster is addressed by its Service name, such as
`http://<service>.<namespace>.svc:<port>/`. Plane refuses webhook URLs on private addresses,
except for the cluster's pod and service networks, allowed by `WEBHOOK_ALLOWED_IPS` in
[`secrets.manifest`](secrets.manifest). Plane signs each delivery with the webhook's secret
(the `X-Plane-Signature` header) and lists the deliveries under the webhook.

The smoke checks `plane_api_work_item` and `plane_webhook_in_cluster` do the same with a
temporary key and webhook in the workspace `smoke`: they switch the work item `smoke probe` in
project SMOKE between Todo and Done, and delete the key, the webhook and their echo endpoint
afterwards.
