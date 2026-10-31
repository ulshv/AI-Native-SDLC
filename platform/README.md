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
- every required external secret is in `~/.config/ai-native-sdlc/secrets.env`. For now that
  is only your Plane admin email:

  ```bash
  mkdir -p ~/.config/ai-native-sdlc && chmod 700 ~/.config/ai-native-sdlc
  echo 'PLANE_ADMIN_EMAIL=you@example.com' >> ~/.config/ai-native-sdlc/secrets.env
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
put them into `secrets.env` yourself; for now that is only `PLANE_ADMIN_EMAIL`. Values persist
across rebuilds, so services keep the same credentials.

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
| Traffic to pods bypasses VPN routing rules, so a VPN in TUN mode cannot capture pods' DNS | `ip rule` priority 100 for `10.42.0.0/16`, re-added on every k3s start by `/etc/systemd/system/k3s.service.d/ai-native-sdlc-routing.conf` |
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

# 3. the routing rule for pods
sudo rm -f /etc/systemd/system/k3s.service.d/ai-native-sdlc-routing.conf \
  && sudo ip rule del to 10.42.0.0/16 lookup main priority 100
```

Firefox keeps the CA it already imported. Remove it under Settings → Privacy & Security →
Certificates → View Certificates → Authorities (`ai-native-sdlc local CA`). `~/.kube/config`
and the secrets directory stay; delete them by hand if you want them gone.

## Repository and origin

Phase 4 makes the platform's Gitea the home of this repository:

- creates the org `sdlc` and the private repository `sdlc/ai-native-sdlc`;
- renames the old `origin` (GitHub) to `github`, so nothing it references is lost, and adds
  `https://gitea.sdlc.internal/sdlc/ai-native-sdlc.git` as `origin`;
- pushes every local branch and tag, and sets `main` to track `origin/main`;
- creates two Gitea tokens for the admin: `bootstrap-push` (write access to repositories) in
  `~/.config/ai-native-sdlc/gitea-push-token`, and `argocd-read` (read only) in Argo CD's
  repository Secret. Both belong to this cluster and are replaced after a rebuild.

Until the ingress is up, phase 4 reaches Gitea through a temporary port-forward. Afterwards a
plain `git push` goes to `gitea.sdlc.internal`: a credential helper in this clone's
`.git/config`, scoped to that host, reads the push token from the secrets directory when git
asks, so the token itself is never written into `.git/config` or `~/.git-credentials`.

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
  or raise the pinned `targetRevision` in its `app.yaml`, commit, and push to `main`. Argo CD
  notices within about three minutes. `platform/smoke.sh gitops_synced_to_main` waits until
  every Application is Synced and Healthy at the commit you pushed. Until the ingress exists,
  `platform/bootstrap.sh` does the push through a port-forward.
- **Manual edits do not stick.** Anything changed with `kubectl edit` or in k9s is reverted
  within seconds. Change git instead.
- **To add a component**, create `apps/<component>/` with an `app.yaml` (sync wave, automated
  sync) and its values or manifests. Components whose configuration is not written yet are
  listed in the root Application's `exclude`.
- **Watch it** with `kubectl get applications -n argocd`, or `:applications` in k9s.

The bootstrap installed Argo CD, CloudNativePG and Gitea with Helm once, then deleted Helm's
release records, so `helm list -A` is empty on purpose. Re-running the bootstrap leaves
anything Argo CD manages alone.

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
| Plane | <https://plane.sdlc.internal> | arrives with the Plane section below |

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
sure.

**When the hook refuses a push**, the secret is in a commit on `main`, so it is in Gitea too.
Treat it as leaked: rotate it in `secrets.env` and re-run the bootstrap (see Secrets). Then
remove it from history, for example with `git rebase -i` for a recent commit or
`git filter-repo --replace-text` for an old one, force-push the rewritten `main` to Gitea, and
push to GitHub again.

`platform/smoke.sh mirror_only_main` checks that GitHub holds `main` and nothing else.

## Plane
