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

## Host changes and removal

## Repository and origin

## Changing the platform through git

## Service URLs

## Public mirror

## Plane
