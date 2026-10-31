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

**Generated or external.** `generated` values are random and created by the bootstrap when
they are missing, so you never type them. `external` values come from somewhere else, and you
put them into `secrets.env` yourself: `PLANE_ADMIN_EMAIL` always, `GITHUB_MIRROR_TOKEN` only for
the public mirror. Values persist across rebuilds, so services keep the same credentials.

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

## Repository and origin

## Changing the platform through git

## Service URLs

## Public mirror

## Plane
