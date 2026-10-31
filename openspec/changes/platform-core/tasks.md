# Tasks

> **TL;DR**
> - **12 groups, in bootstrap-phase order:** scaffolding → preflight → secrets → host and k3s → database → Gitea → repo and origin move → GitOps hand-over → ingress and TLS → public mirror → Plane → end-to-end checks.
> - **Each group ships its own checks** in `platform/smoke.sh` and its own section in `platform/README.md`. Nothing is left for a final "tests and docs" group.
> - **Verified at the end:** a fresh bootstrap and a full rebuild pass every smoke check. Data survives a reboot, names work offline and after a network switch, and the RAM estimate in the README is replaced with measured numbers.
> - **Spikes first in the Plane group:** webhooks to private addresses (11.1) and automatic admin creation (11.2). Either one can send us back to the spec.
> - **Manual steps for you:**
>   - the sudo password during bootstrap;
>   - `PLANE_ADMIN_EMAIL` in `secrets.env`;
>   - freeing ports 80 and 443 (for example, by stopping Docker Desktop);
>   - restarting the browser after the CA is first trusted;
>   - `git push github main` by hand whenever `main` should be published;
>   - a reboot and turning the network off and back on in the final checks.

## 1. Scaffolding

- [x] 1.1 Create the `platform/` layout from design decision 11 (`argocd/`, `apps/<component>/` for every component) and verify the tree matches the design
- [x] 1.2 Add `.gitignore` rules for `secrets.env`, `*.key` and `.secrets/`, and verify with `git check-ignore` that a test `secrets.env` and `ca.key` placed in the working tree are ignored
- [x] 1.3 Pin the latest stable versions of k3s and of the Argo CD, cert-manager, CloudNativePG, Traefik, Gitea and Plane CE charts in the components' `app.yaml` files, and verify no `targetRevision` is a moving tag such as `latest` or `*`
- [x] 1.4 Create `platform/smoke.sh`, which runs named checks and exits non-zero on the first failure, and verify it runs cleanly with no checks registered
- [x] 1.5 Create `platform/README.md` with the run command, a link to "Host prerequisites" in the root `README.md` (the single place that lists packages to install), and empty sections for later groups. Verify every command it lists exists in the repo or on the host

## 2. Bootstrap skeleton and preflight

- [x] 2.1 Create `platform/bootstrap.sh` with the phase structure from design decision 2, where every phase first checks the current state and skips when already done, and verify a dry run prints the phase plan without changing anything
- [x] 2.2 Implement preflight: sudo available; required tools present (`helm`, `git`, `openssl`, `jq`, `yq`, `curl`, `certutil`); ports 80 and 443 free, naming the process that holds them; required external secrets present. Verify that each missing prerequisite makes the script exit non-zero with its name, and that the host is unchanged
- [x] 2.3 Document the preflight requirements and how to free ports 80 and 443 (for example, stopping Docker Desktop) in `platform/README.md`

## 3. Secrets

- [x] 3.1 Write `platform/secrets.manifest` with every secret from design decision 6 (name, `generated` or `external`, required flag, target namespace, Secret and key), and generate `platform/secrets.env.example` from it. Verify the example lists every name and contains no values
- [x] 3.2 Implement secret loading and generation into `~/.config/ai-native-sdlc/secrets.env` (mode 0600). Verify that a fresh directory gets every generatable value, that a second run leaves the file's checksum unchanged, and that a missing `PLANE_ADMIN_EMAIL` fails preflight
- [x] 3.3 Implement root CA generation (1-year validity) into `~/.config/ai-native-sdlc/ca.{key,crt}` when missing, and verify with `openssl x509 -enddate` that the expiry is one year out
- [x] 3.4 Add smoke checks: no value from `secrets.env` appears in any tracked file, and the working tree holds no secret material. Verify both pass, and that the first one fails when a value is planted in a tracked test file
- [x] 3.5 Document `secrets.env`, generated versus external values, rotation and backing up the secrets directory in `platform/README.md`

## 4. Host and cluster

- [x] 4.1 Implement k3s installation with `--disable traefik` (ServiceLB and local-path kept) and kubeconfig access for the operator. Verify `kubectl get nodes` shows the node Ready, and that a re-run does not reinstall k3s
- [x] 4.2 Implement applying every secrets manifest entry as a Kubernetes Secret through `kubectl apply` with dry-run output (namespaces created first). Verify each target Secret exists, and that changing a value and re-running updates it (rotation)
- [x] 4.3 Create `platform/hosts.txt` (`gitea`, `plane`, `argocd`) and implement the managed `/etc/hosts` block. Verify `getent hosts gitea.sdlc.internal` returns `127.0.0.1`, and that a re-run leaves `/etc/hosts` byte-identical
- [x] 4.4 Implement host CA trust: the system store via `update-ca-certificates`, `~/.pki/nssdb` via `certutil`, and a Firefox `Certificates.Install` policy in `/etc/firefox/policies/`. Verify the CA appears in all three
- [x] 4.5 Implement CA rotation when fewer than 30 days remain: a new CA, trust added for it, trust removed for the old one, and the Secret updated. Verify by temporarily swapping in a CA valid for 10 days (after backing up the real one), re-running, and checking that the new CA is trusted and the old one is not
- [x] 4.6 Document the host changes and the two commands that remove the hosts block and the CA trust after `k3s-uninstall.sh` in `platform/README.md`, and verify those commands restore a clean `/etc/hosts` and trust store
- [x] 4.7 Keep traffic to pods on the main routing table ahead of VPN policy rules (`ip rule` priority 100 for `10.42.0.0/16`, re-added by a k3s unit drop-in), and add a smoke check that a pod resolves a Service name. Verify the check fails with a TUN-mode VPN running and passes after the bootstrap, and that the rule is back after `systemctl restart k3s`

## 5. Shared database

- [x] 5.1 Write `platform/apps/cloudnative-pg/` values (the operator), and in `platform/apps/databases/` the `platform-pg` Cluster with `managed.roles` for `gitea` and `plane` (passwords from the seeded Secrets), plus `Database` resources owned by those roles. Pin the Postgres image with `imageName`, so an operator upgrade cannot change the Postgres version
- [x] 5.2 Add `pg_hba` rules to the `platform-pg` Cluster: each service role may connect only to its own database, and every other network connection is rejected
- [x] 5.3 Implement bootstrap phase 3 for the CNPG operator and cluster with Helm and `kubectl`, reading the same files, and verify the cluster reports healthy
- [x] 5.4 Add a smoke check: the `gitea` credentials can open the `gitea` database and are refused on `plane`. Verify it passes

## 6. Gitea

- [x] 6.1 Write `platform/apps/gitea/values.yaml`: `postgresql-ha` and `valkey-cluster` disabled, database `platform-pg/gitea`, built-in cache, session and queue, `ROOT_URL=https://gitea.sdlc.internal/`, no SSH service, admin from the seeded Secret, a local-path PVC, explicit resource requests and limits, and the chart's helm-test pod disabled (`test.enabled: false`, it uses `busybox:latest`)
- [x] 6.2 Implement Gitea installation in bootstrap phase 3 with the same values file, and verify through a port-forward that the API answers and the admin can authenticate

## 7. Repository and origin

- [x] 7.1 Implement phase 4: start a temporary port-forward to Gitea's Service, create the org `sdlc` and the private repo `ai-native-sdlc` through the API if missing, and create the push token and the Argo CD read token. Verify that a re-run creates nothing new
- [x] 7.2 Implement the origin move: rename `origin` to `github` when it points elsewhere (and fail if `github` exists with a different URL), add the Gitea URL as `origin`, push all branches and tags through the port-forward, and set `main` to track `origin/main`. Verify every local branch and tag exists on Gitea with the same commits, and that a re-run leaves the remotes unchanged
- [x] 7.3 Configure the git credential helper scoped to `https://gitea.sdlc.internal`, which reads the push token from the secrets directory. Verify the token appears in neither `.git/config` nor `~/.git-credentials`
- [x] 7.4 Document the origin move and its rollback (`git remote remove origin && git remote rename github origin`) in `platform/README.md`

## 8. GitOps hand-over

- [x] 8.1 Write the child Applications with sync waves (-2, -1, 0), automated sync with `prune` and `selfHeal`, and `ServerSideApply=true`, plus `platform/argocd/root.yaml` pointing at the in-cluster Gitea URL with the read token
- [x] 8.2 Implement phase 5: install Argo CD with Helm from `platform/apps/argocd/values.yaml` and apply the root Application. Verify the root and all child Applications appear
- [x] 8.3 Implement phase 6: wait until every Application is Synced and Healthy, delete the `sh.helm.release.v1.*` Secrets of the releases from phases 3 and 5, and make phases 3 and 5 skip when their Applications exist. Verify that `helm list -A` shows no releases afterwards, that no pod restarted during adoption, and that a re-run changes nothing
- [x] 8.4 Add smoke checks for reconciliation that never commit to `main`: every Application becomes Synced and Healthy at the current `main` on Gitea within 5 minutes (run after a push), a manual edit to a managed probe ConfigMap is reverted within 5 minutes, and every Argo CD setting in its `values.yaml` is live in Argo CD's own ConfigMaps. Verify all three pass, and once by hand that an Argo CD setting pushed to `main` is applied to Argo CD itself
- [x] 8.5 Document how to change the platform through git and the recovery procedure for a broken Argo CD (`helm upgrade` with the last good values) in `platform/README.md`
- [x] 8.6 Add a smoke check `no_floating_images`: every container image of every pod in the cluster has an explicit tag or digest, and none is `latest`. Verify it passes, and that it fails while a throwaway pod with an untagged image is running

## 9. Ingress and TLS

- [x] 9.1 Write the cert-manager app (wave -2) and the CA `ClusterIssuer` plus the `*.sdlc.internal` Certificate in the `traefik` namespace (wave -1), and verify the Certificate is Ready and chains to the root CA
- [x] 9.2 Write the Traefik app with ServiceLB, the default `TLSStore` using the wildcard certificate, and an HTTP→HTTPS redirect on the `web` entrypoint. Verify early that `127.0.0.1:443` reaches Traefik; if it does not, switch to the `hostNetwork` fallback from the design
- [x] 9.3 Write the `coredns-custom` ConfigMap that rewrites `*.sdlc.internal` to Traefik's Service, and verify from a throwaway pod that `gitea.sdlc.internal` resolves and that HTTPS succeeds when the CA is supplied explicitly
- [x] 9.4 Add ingress routes for Gitea and Argo CD, plus smoke checks: every name in `hosts.txt` answers over HTTPS with `curl` and no `-k`, and `http://` redirects to `https://`. Verify both pass and that Chrome and Firefox show no warnings
- [x] 9.5 Add the smoke check that a new name added to `hosts.txt` resolves from the host and pods after a re-run, and verify it with a temporary name
- [x] 9.6 Document the service URLs and the browser restart needed after the first CA trust in `platform/README.md`

## 10. Public mirror

- [x] 10.1 Add `platform/hooks/pre-push`. For a push to a `github.com` remote, it refuses every ref except `main` and every tag, refuses the push when a pushed commit contains a value from `secrets.env` (naming the file, never the value), and runs gitleaks on the pushed commits when it is installed. Have the bootstrap set `core.hooksPath` to `platform/hooks`. Verify against a local bare repository whose path contains `github.com`: `main` is pushed, another branch and a tag are refused, a commit with a planted value is refused, and a push to Gitea is not checked
- [x] 10.2 Add a smoke check that the public mirror on GitHub holds no branch other than `main` and no tag. Verify it passes
- [x] 10.3 Push `main` to `github` by hand and verify that the hook lets it through and that GitHub's `main` matches the local one
- [x] 10.4 Document publishing to the mirror, what the hook checks, and what to do when it refuses a push (rewrite the history and rotate the leaked secret) in `platform/README.md`

## 11. Plane CE

- [x] 11.1 Spike: install Plane and verify it delivers a webhook to an in-cluster endpoint addressed by its service name. If Plane refuses private addresses, look for a setting that allows them. Report back before building further if neither works
- [x] 11.2 Spike: find a supported way to create the instance admin non-interactively (a management command in the API image or similar). If none exists, apply the design's fallback and update the `issue-tracking` spec before continuing
- [x] 11.3 Write `platform/apps/plane/values.yaml`: bundled Postgres disabled, external database `platform-pg/plane`, Redis, RabbitMQ and MinIO from the chart, the `plane.sdlc.internal` ingress without TLS settings, secrets from the seeded Secrets, explicit resource requests and limits, and a pinned tag for the MinIO init image (the chart defaults to an untagged `busybox`). Verify the app is Synced and Healthy, and that a work item created in the UI is saved
- [x] 11.4 Add the idempotent PostSync Job that creates the instance admin from `PLANE_ADMIN_EMAIL` and `PLANE_ADMIN_PASSWORD`. Verify that sign-in works with those credentials after bootstrap, that `/god-mode/` no longer offers admin creation, and that a re-sync leaves the admin unchanged
- [x] 11.5 Add smoke checks: with an API key, read a work item and change its status (visible in the UI), and a status change delivers a webhook to a temporary in-cluster echo endpoint within 1 minute. Verify both pass
- [x] 11.6 Document the admin sign-in, API key creation and webhook setup in `platform/README.md`

## 12. End-to-end verification

- [ ] 12.1 Fresh host: run `k3s-uninstall.sh`, then `platform/bootstrap.sh`, and verify every smoke check passes with no manual step besides the documented ones
- [ ] 12.2 Rebuild: repeat 12.1 and verify the Gitea `main` matches the local clone, the credentials are unchanged, and the browser trusts the certificates without re-importing anything
- [ ] 12.3 Reboot the host and verify the platform comes back, and that a repository, a user and a work item created before the reboot are still present
- [ ] 12.4 Turn the network off, then switch to another network, and verify every service name keeps working from the host and from a pod
- [ ] 12.5 Measure RAM with the platform idle and update the "Platform on one laptop" estimate in `README.md` and `README_ru.md` with the measured values

## Workflow follow-up

- Review the implementation against the specs, then archive the change with `/opsx:archive` so the deltas merge into `openspec/specs/`.
- After archive, check that the seven new main specs exist under `openspec/specs/` and keep their Purpose sections.
