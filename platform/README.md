# Platform

The platform core (k3s, Argo CD, Postgres, Gitea, Plane) for this repository, built and
rebuilt by one command. After the bootstrap, every change to the platform is a git push.

## Run

Install the [host prerequisites](../README.md#host-prerequisites) first, then from the
repository root:

```bash
platform/bootstrap.sh       # build or rebuild the platform; safe to re-run
platform/accounts.sh        # the people's accounts from accounts.yaml (see People); safe to re-run
platform/seed-projects.sh   # connect the target projects (see Target projects); safe to re-run
platform/smoke.sh           # check that it works
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
- `ca.key`, `ca.crt`: the local root CA behind every `*.sdlc.internal` certificate;
- `accounts.yaml`: the people's accounts in Plane and Gitea (see People under Target projects).

[`secrets.manifest`](secrets.manifest) is the contract: which secrets exist and where each
goes in the cluster. [`secrets.env.example`](secrets.env.example) lists the names with a
description of each.

**Generated or external.** `password` and `key` values are random base58 strings that the
bootstrap creates when they are missing, so you never type them. A `password` (22 characters,
128 bits) is only hashed or compared; a `key` (44 characters, 256 bits) is turned into an
encryption or signing key by the app. `external` values come from somewhere else, and you
put them into `secrets.env` yourself: `PLANE_ADMIN_EMAIL`, `GITEA_ADMIN_EMAIL` and `LLM_API_KEY`. Values persist
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
| BuildKit's own AppArmor and seccomp profiles for the CI pods (see [CI](#ci)) | `/etc/apparmor.d/sdlc-buildkit`, `/var/lib/kubelet/seccomp/profiles/sdlc-buildkit.json`, from [`apparmor/`](apparmor/sdlc-buildkit) and [`seccomp/`](seccomp/sdlc-buildkit.json) |
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

# 4. the registry access (k3s-uninstall.sh removes it with the rest of /etc/rancher), and
#    BuildKit's profiles
sudo rm -f /etc/rancher/k3s/registries.yaml /var/lib/kubelet/seccomp/profiles/sdlc-buildkit.json \
  && sudo apparmor_parser -R /etc/apparmor.d/sdlc-buildkit && sudo rm -f /etc/apparmor.d/sdlc-buildkit

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

- creates the organizations `platform` (this repository and the platform's images) and `sdlc`
  (the target projects), and the private repository `platform/ai-native-sdlc`; a repository
  still at `sdlc/ai-native-sdlc` from an earlier version is moved there, with its history;
- renames the old `origin` (GitHub) to `github`, so nothing it references is lost, and adds
  `https://gitea.sdlc.internal/platform/ai-native-sdlc.git` as `origin`;
- pushes every local branch and tag, and sets `main` to track `origin/main`;
- creates the admin's Gitea token `bootstrap-push` (write access to repositories) in
  `~/.config/ai-native-sdlc/gitea-push-token`;
- creates the service accounts below, with tokens that live only where they are used.

| Account | What it is for | Its token |
|---|---|---|
| `agent` | the orchestrator: comments, pull requests, merges into `development` where a target project allows it | `sdlc/gitea-agent` (write to repositories and issues) |
| `agent-runner` | agent runs: pushes feature branches, may merge nothing | `sdlc-agents/gitea-agent-runner` (write to repositories) |
| `reader` | reads every repository and package of `platform` and `sdlc` (team `readers` in both) | `argocd/creds-platform` and `argocd/creds-sdlc` (Argo CD's credential templates, one per organization) and `/etc/rancher/k3s/registries.yaml` (image pulls, phase 7) |
| `ci` | the target projects' CI: pushes their images and creates their releases (team `ci` of `sdlc`) | the Actions secret `CI_REGISTRY_TOKEN` of the `sdlc` organization |
| `ci-platform` | this repository's CI: pushes the platform's images (team `ci` of `platform`) | the Actions secret `PLATFORM_REGISTRY_TOKEN` of `platform/ai-native-sdlc` alone |

Nobody signs in as a service account: its password is random and never stored. Every token
belongs to this cluster and is replaced after a rebuild; a re-run replaces only a token that
no longer works. Which repositories `agent` and `agent-runner` may write to, and which
branches they may merge into, is set per target project by `seed-projects.sh`.

Until the ingress is up, phase 4 reaches Gitea through a temporary port-forward. Afterwards a
plain `git push` goes to `gitea.sdlc.internal`: a credential helper in this clone's
`.git/config`, scoped to that host, reads the push token from the secrets directory when git
asks, so the token itself is never written into `.git/config` or `~/.git-credentials`.

**Rollback** to GitHub as `origin`:

```bash
git remote remove origin && git remote rename github origin
git config --remove-section credential.https://gitea.sdlc.internal
```

## Container registry

Images live in Gitea's own registry, and a package belongs to an organization, not to a
repository, so write access to it is granted per organization. Hence two of them:

- `gitea.sdlc.internal/platform/<name>:<commit>`: the platform's own images (the orchestrator,
  the agent runner), pushed only by this repository's CI;
- `gitea.sdlc.internal/sdlc/<name>:<commit>`: the target projects' images, pushed by their CI.

The target projects' workflows are written by agents and run on every push, before anyone
reviews them, so their token must not reach the platform's images; it can push to `sdlc`
only. A repository collaborator is refused, and so is Gitea Actions' own job token.

Phase 7, the last one, lets k3s pull from both in any namespace without a pull secret: it
writes `/etc/rancher/k3s/registries.yaml` (mode 0600) with `reader`'s read-only package token
and the local CA, and restarts k3s, which reads the file only on start. Pods keep running
through the restart; the API is away for about half a minute. A re-run leaves the file and k3s
alone while the token works. It comes last because the token needs Gitea, which runs only
from phase 3.

The smoke check `registry_pull` runs the newest image of either organization in a pod, asking
the registry each time; while the registry is empty it only says so.

**Undo:**

```bash
sudo rm /etc/rancher/k3s/registries.yaml && sudo systemctl restart k3s
```

## CI

Two Gitea Actions runners in namespace `ci` ([`apps/ci/`](apps/ci/)), each in a pod of its
own:

| Runner | Registered on | Label | Runs | Pushes with |
|---|---|---|---|---|
| `ci-platform` | `platform/ai-native-sdlc` only | `platform` | this repository's workflows | `ci-platform` → `platform/` |
| `ci-products` | the `sdlc` organization | `ubuntu-latest` | the target projects' workflows | `ci` → `sdlc/` |

A workflow picks its runner with `runs-on`. This repository's workflows use `platform`;
a target project's `ubuntu-latest` never reaches `ci-platform`, which does not carry that
label and is not registered on other repositories.

**How a runner works.** Gitea's public runner image (Ubuntu 26.04 with Node, git and curl)
runs the jobs in host mode, with `act_runner` copied in from its official image; there is no
Docker daemon. Each registration takes a single job (`--ephemeral`): when it ends, Gitea drops
the runner, the work directory is wiped, and the runner registers again. The container and its
BuildKit live on between jobs, so the jobs of one runner are not isolated from each other; they
share a trust level, which is why the platform's jobs never run on the products' runner. A workflow builds images with `buildctl` against the pod's BuildKit sidecar
(`unix:///run/buildkit/buildkitd.sock`) and pushes them with its organization's token.

**Isolation.** CI runs code an agent wrote. Each runner pod has its own user namespace
(`hostUsers: false`): root inside is an unprivileged uid on the host. BuildKit runs there in
its regular mode with `SYS_ADMIN`, which holds only inside that namespace, under the AppArmor
profile [`apparmor/sdlc-buildkit`](apparmor/sdlc-buildkit) and the seccomp profile
[`seccomp/sdlc-buildkit.json`](seccomp/sdlc-buildkit.json) that phase 2 installs. Nothing is
privileged and no host path is mounted. A NetworkPolicy lets the pods reach DNS, Gitea, Traefik
(the registry), each other and the internet, and no private network. The smoke checks
`ci_runners` and `ci_unprivileged` check both.

**Platform images.** [`.gitea/workflows/images.yaml`](../.gitea/workflows/images.yaml) runs on
every push to `main`. Three checks run first, and an image is built only when all pass:

- `test`: the orchestrator's lint, types and tests, against a Postgres in the runner;
- `scripts`: shellcheck on every script, with the repository's `.shellcheckrc`;
- `manifests`: [`manifests.sh check`](manifests.sh) renders what Argo CD applies from this
  repository (the Applications, every directory source, the orchestrator's chart, the project
  files' ConfigMap) and checks it with kubeconform against Kubernetes' and the CRDs' schemas, and
  the project files against their schema. The external charts are not rendered.

Then each of `orchestrator/` and `agent-runner/` that has a `Dockerfile` is built and pushed as
`gitea.sdlc.internal/platform/<directory>:<commit>`. Argo CD deploys the orchestrator with the
tag of the commit it syncs, so a push to `main` reaches the cluster through CI and GitOps alone.
Locally, `KUSTOMIZE="kubectl kustomize" platform/manifests.sh check` needs helm and kubeconform.

**After a rebuild** there are no images yet: phase 4 pushes `main` before the runners exist,
Gitea keeps that run queued, and `ci-platform` picks it up as soon as Argo CD has started it.
Until the run finishes, the orchestrator's pod waits for its image.

## Target projects

A product joins the delivery loop with one directory, `projects/<name>/`, whose `project.yaml`
names its Plane project, its Gitea repository and local clone, its branches, its domain and its
people with their roles ([`projects/example-webapp/project.yaml`](projects/example-webapp/project.yaml)).
[`projects/project.schema.json`](projects/project.schema.json) is the format, checked by the
smoke check `project_files_valid`. The product's code lives in its own repository, in the Gitea
organization `sdlc`, never in this one.

**Its repository.** [`seed-projects.sh`](seed-projects.sh), run after the bootstrap, connects
every project to Gitea; it needs no sudo and is safe to re-run:

- checks first that every project's local clone exists with both long-lived branches, and stops
  before changing anything when one does not, naming it;
- creates a missing repository from its clone, with every branch and tag; an existing one keeps
  its contents, so after a rebuild the clone is what comes back;
- sets the default branch to the base branch (`development`), so pull requests go there;
- gives `agent` and `agent-runner` write access, and the project's people access by role; a
  person without a Gitea user stops it, named (see People);
- protects both long-lived branches: nobody pushes to them directly; `development` takes merges
  from the engineers and `agent`, without a required review (the orchestrator's policy is the
  gate there); `main` takes merges only from the engineers, after an approval by one of the
  project's people;
- points the clone's `origin` at Gitea when it has none.

The clone's place is `repository.clone` in the project file (`~/Projects/sdlc/example-webapp`);
`<NAME>_CLONE` in the environment overrides it, such as
`EXAMPLE_WEBAPP_CLONE=/elsewhere platform/seed-projects.sh`. `--dry-run` prints the plan.

**Adding a project:**

1. create its repository locally with `main` and `development`, an OpenSpec setup for OpenCode,
   a `deploy/chart` and CI workflows (see `example-webapp`);
2. add `projects/<name>/project.yaml` and one line in
   [`projects/kustomization.yaml`](projects/kustomization.yaml);
3. push this repository: Plane's setup Job creates the Plane project, and the orchestrator reads
   the new file without a restart;
4. run `platform/seed-projects.sh`.

### People

The platform has three kinds of accounts:

| Accounts | For | Made by |
|---|---|---|
| the instance admins: `admin` in Plane, `gitea_admin` in Gitea, `admin` in Argo CD | administering the tools; until SSO, also operating the platform | the bootstrap and the charts, from `PLANE_ADMIN_*` and `GITEA_ADMIN_*` in `secrets.env`; Argo CD's from its own `argocd-initial-admin-secret` |
| the services: `agent`, `agent-runner`, `reader`, `ci`, `ci-platform` | the orchestrator, agent runs, Argo CD and k3s reading, CI | the bootstrap and Plane's setup Job; their tokens live only in cluster Secrets |
| people: `sergey`, `manager`, `dev` | working in the loop: tickets, reviews, merges | [`accounts.sh`](accounts.sh), from `~/.config/ai-native-sdlc/accounts.yaml` |

A person has an account, kept by the platform, and roles, kept by each project in git:

- **the account**, in `~/.config/ai-native-sdlc/accounts.yaml` (the format:
  [`accounts.yaml.example`](accounts.yaml.example)): `username`, `email`, `firstName`,
  `lastName`, `enabled`, Keycloak's field names, so that SSO takes the file as it is. It is not
  in git, because the repository is public. `accounts.sh` makes Plane and Gitea match it, with
  the same username in both;
- **a project's roles**, in the `people` of its project file: a name, the roles
  (`product_owner`, `engineer`), and the person's usernames in Plane and Gitea. The orchestrator
  checks every request against it.

Accounts and roles are meant to move to Keycloak, with finer roles; what that takes is in
[`docs/identity-and-access.md`](../docs/identity-and-access.md).

From the project file the rest is derived:

| Derived | By | Where |
|---|---|---|
| membership of the Plane workspace and project | Plane's setup Job, after every sync of Plane | Plane |
| access to the repository: Write for an engineer, Read for a product owner, who needs it to approve | `seed-projects.sh` | Gitea |
| who merges into `development` and `main`, whose approval counts | `seed-projects.sh` | the branch protections in Gitea |
| `people.json`: Plane username → Plane user id, for `agent` and every person with a Plane account | Plane's setup Job | the Secret `sdlc/plane-agent`, mounted into the orchestrator at `/plane/people.json` |

The orchestrator needs `people.json` because Plane's webhooks and API name users by id, not by
username: to tell who wrote a comment, and to assign a ticket. The ids change with every
rebuild, so the file is generated and not in git; never edit it. The orchestrator reads the
project files and `people.json` again when they change, with no restart. To see it:
`kubectl get secret -n sdlc plane-agent -o jsonpath='{.data.people\.json}' | base64 -d`.

**Adding a person:**

1. add their account to `accounts.yaml` and run `platform/accounts.sh`. It creates them in both
   tools and shows their first password once; hand it over. They sign in to Plane with the
   e-mail address and to Gitea with the username, which asks for a new password at once; in
   Plane, Profile settings → Security changes it;
2. add them to `people` in the project file with their roles, and push. Argo CD syncs Plane,
   and its setup Job makes them a member and adds them to `people.json`. A few minutes after
   the push the orchestrator knows them;
3. run `platform/seed-projects.sh`: their access to the repository and the branch protections.

**Changing:** roles in the project file (push, then `seed-projects.sh`); a name or an e-mail
address in `accounts.yaml` (`accounts.sh`); a forgotten password with
`platform/accounts.sh --reset <username>`, which shows a new one. `accounts.sh --dry-run` shows
what it would change, and the smoke check `accounts_current` that nothing is left to.

**Removing a person:** delete them from the project file, push, and run `seed-projects.sh`:
the orchestrator stops taking their requests once it reads the file again. To lock them out of
Plane and Gitea, set `enabled: false` in `accounts.yaml` and run `accounts.sh`. Their repository
access and Plane membership stay until an admin removes them.

## Where to change what

| To change | Edit | Takes effect |
|---|---|---|
| a project, its people and roles, its checks | `projects/<name>/project.yaml` | push; the Gitea side after `seed-projects.sh` (Target projects) |
| a person's account: name, e-mail address, locked or not | `~/.config/ai-native-sdlc/accounts.yaml` | `platform/accounts.sh` (People) |
| the orchestrator's models, workers, limits | `config` in [`apps/orchestrator/chart/values.yaml`](apps/orchestrator/chart/values.yaml), the settings in [`orchestrator/README.md`](../orchestrator/README.md#configuration) | push; Argo CD rolls the pod over |
| the models agent runs may use | [`apps/agents/opencode-config.yaml`](apps/agents/opencode-config.yaml) | push; the next run |
| a platform component | `apps/<component>/values.yaml` or its manifests | push (Changing the platform through git) |
| a secret: the model key, webhook secrets, admin passwords | `~/.config/ai-native-sdlc/secrets.env` | `platform/bootstrap.sh`, then restart what uses it (Secrets) |
| a host name | [`hosts.txt`](hosts.txt) | `platform/bootstrap.sh` |

Never by hand: the Secrets the platform writes for itself, such as `sdlc/plane-agent` (agent's
Plane key, `people.json`), and anything Argo CD manages: `kubectl edit` is reverted within
seconds.

## Agent runs

Each agent run is a Kubernetes Job in namespace `sdlc-agents` (design decision 7), started by
the orchestrator, or by hand with [`run-agent.sh`](run-agent.sh). Its image,
[`agent-runner/`](../agent-runner/), is OpenCode and the OpenSpec CLI, pinned, with an entrypoint
that does everything around the model: it clones the target project over Gitea's in-cluster
service, checks out the ticket's branch (`feat/EWA-12_<slug>`, from `development` when new), runs
one OpenCode command, runs the project's checks, commits, pushes, and ends with one result.

| Mode | What it does | Commits | Result |
|---|---|---|---|
| `propose` | `/opsx:propose` for the ticket, with the answers and feedback so far | only `openspec/changes/<change>/`, validated | `spec_ready` |
| `apply` | `/opsx:apply` of the change, then the project's `checks` (`npm ci`, tests, ...) | the code, never `.gitea/`; the change stays unarchived | `code_ready` |
| `fix` | the same, with the review's feedback | the same | `code_ready` |
| `archive` | `openspec archive` without a model, then validates every spec | `openspec/` | `archived` |

When the agent lacks information it writes its questions to `.agent/result.json`, as the target
project's `AGENTS.md` asks, and the run ends with `questions` and pushes nothing. Anything else
ends with `failed` and a reason. The result is the pod's termination message, so nothing parses
logs: `kubectl get pod -n sdlc-agents -l job-name=<job>
-o jsonpath='{.status.containerStatuses[0].state.terminated.message}'`.

**Limits and isolation.** 30 minutes, no retry, the Job kept for an hour. The pod runs as a
user, with no capabilities, a read-only root file system and no Kubernetes token. Its only
secrets are the model provider's key (`sdlc-agents/llm-api-key`) and `agent-runner`'s Gitea
token, which may push branches but merge nothing. A NetworkPolicy lets it reach DNS, Gitea and
the internet, and no private network: neither the database, Plane, Traefik nor the Kubernetes
API, which the smoke check `agents_network` tries.

**The model** is configuration: [`apps/agents/opencode-config.yaml`](apps/agents/opencode-config.yaml)
has one OpenAI-compatible provider block (RouterAI) and the models a run may use; the run picks
one with `--model llm/<model>`. Another provider is another `baseURL` and key, with the same image.

**By hand:**

```bash
platform/run-agent.sh example-webapp propose EWA-0 --title "Orders filter" \
  --text "Filter the orders table by status."
platform/run-agent.sh example-webapp apply EWA-0 --slug orders-filter
platform/run-agent.sh example-webapp archive EWA-0 --slug orders-filter
```

It builds the task from the project file, starts the Job with the image of `origin/main`
(`--image-tag` for another), waits, and prints the run's log lines, among them the agent's last
words, and its result. `--entrypoint agent-runner/entrypoint.sh` runs the local entrypoint instead
of the image's, to try a change to it without a commit. The branch stays in Gitea; delete it when
done.

**Reading a failure.** The result's `reason` says what failed: a check with the end of its output,
OpenCode's own error, or "changed nothing it may commit" with the agent's last words, which a
cheap model sometimes ends on (start the run again). The entrypoint removes the model provider's
key from everything it writes.

## Orchestrator

The orchestrator ([`orchestrator/`](../orchestrator/README.md)) runs in namespace `sdlc` from
[`apps/orchestrator/`](apps/orchestrator/), a Helm chart that Argo CD renders with the commit it
syncs as the image tag:

- a PreSync Job runs `orchestrator migrate` with the new image, so the schema is current before
  the new pod starts; the service never migrates on start;
- one Deployment with a rolling update that keeps the old pod until the new one is ready: a
  commit whose image never builds leaves the running version alone;
- the Service `orchestrator.sdlc.svc`, where both tools' webhooks go;
- its configuration from `config` in [`chart/values.yaml`](apps/orchestrator/chart/values.yaml),
  the project files as the ConfigMap `target-projects`, and a Role in `sdlc-agents` to start and
  watch agent runs.

Plane's webhook is set up by its setup Job, and the `sdlc` organization's Gitea webhook by the
bootstrap. Gitea never shows a webhook's secret again, so after `GITEA_WEBHOOK_SECRET` changes,
delete the webhook in Gitea and run the bootstrap again. The smoke check `orchestrator_running`
checks the rollout, `/readyz` and the Gitea webhook.

### Watching an item

A ticket's status in Plane is its phase, and the agent says in the ticket and in its pull request
what it does. Behind them, through `kubectl port-forward -n sdlc svc/orchestrator 8000:80`:

```bash
curl -s "localhost:8000/threads?link=ticket:example-webapp/EWA-2"   # the item's thread and links
curl -s localhost:8000/threads/<thread>                             # phase, branch, change, PR
curl -s localhost:8000/threads/<thread>/messages                    # people's requests, answers
kubectl get jobs -n sdlc-agents -l sdlc/key=ewa-2                   # its runs, kept for an hour
kubectl logs -n sdlc deploy/orchestrator | jq 'select(.thread == "<thread>")'
```

The logs are the running pod's: a restart starts them anew.

### When a run fails

- **The run failed** (OpenCode stopped, a check failed, it changed nothing): the ticket moves to
  Needs info with the reason and the agent's last words. Any comment with `@agent` runs the same
  mode again, and what it says goes to the agent. The run's own log stays an hour:
  `kubectl logs -n sdlc-agents job/<key>-<mode>-<n>`; its result is the pod's termination
  message (see Agent runs).
- **The agent asked** (Needs info with numbered questions): answer them in one comment with
  `@agent`.
- **A step could not finish** after its attempts (Gitea refused the merge, Plane was down): the
  ticket says "I could not finish a step" with the error. Fix what blocks it, for example the
  pull request's conflict, then ask `@agent` again.
- **Nothing happens** after a comment: the comment must mention `@agent` (Plane's mention, or the
  literal `@agent` in Gitea), and its author must be one of the project's people. An event the
  orchestrator gave up is in its database:
  `kubectl exec -n databases platform-pg-1 -c postgres -- psql -d orchestrator -c "SELECT received_at, source, error FROM inbox WHERE error IS NOT NULL ORDER BY id DESC LIMIT 5"`.

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

**Known issues**, with their symptoms and the way out, are in
[`docs/argocd-issues.md`](../docs/argocd-issues.md): a commit whose image never builds, for one,
holds back the orchestrator's next ones.

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

These are the admins. In the loop, work under your own account from `accounts.yaml` (see People
under Target projects): the orchestrator takes no request from an admin.

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

- it pushes anything but `main` or an `epic/*` branch: another branch, a tag, or the
  deletion of `main`. An epic branch goes there by hand (`git push github epic/<name>`) when an
  outside contributor needs it before it lands in `main`; delete it there once merged;
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
from `secrets.env`, because the data does not survive it. The Job also keeps the admin's
username at `admin` (Plane generates a random one), because project files name people by
username.

**Set up for the delivery loop.** A second Job after every sync
([`apps/plane/setup-job.yaml`](apps/plane/setup-job.yaml), script
[`apps/plane/setup.py`](apps/plane/setup.py)) makes, for every target project in
[`projects/`](projects/), what the loop needs, and changes nothing on a second run:

- the account `agent`, which people assign tickets to and mention, with its e-mail
  notifications off, and its API key in the Secret `sdlc/plane-agent` for the orchestrator;
- the project's workspace (`sdlc`) and project (`EWA`, "Example webapp": Plane allows no
  special characters in a project's name), with the loop's statuses (Drafting, Needs info,
  Spec review, Approved, Implementing, Deploying to QA, Code review, On dev, Released, Closed)
  next to Backlog, where new tickets land. Plane's other default statuses are removed once,
  when the project is created; statuses people add later stay;
- `agent` and the project file's people as members. A person is named by Plane username, which
  must stay the same across rebuilds: the admin's is kept at `admin`. A person without a Plane
  user (`platform/accounts.sh` makes it) is skipped and named in the Job's log; the others'
  Plane ids go to `people.json` in `sdlc/plane-agent` (see People under Target projects);
- one webhook per workspace to `http://orchestrator.sdlc.svc/hooks/plane` for work items and
  comments, signed with `PLANE_WEBHOOK_SECRET`.

Users, API keys and webhooks have no public API, so the script uses Plane's Django models for
them; projects, statuses and members go through Plane's public API, in-process. Plane allows an
API key 60 calls a minute (`API_KEY_RATE_LIMIT`), so the Job waits out a `429` instead of
failing. Nothing needs a manual step. The smoke check `plane_loop_project` checks the statuses,
the members, the webhook and its secret, and agent's key. A new target project needs its file in
`projects/` and one line in [`projects/kustomization.yaml`](projects/kustomization.yaml). See
the Job's log with `kubectl logs -n plane job/plane-setup`.

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
