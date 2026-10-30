# Spec Delta

## Purpose

This repository lives on the platform's own git server, which becomes the default place to push. The original GitHub copy stays, at most, as a read-only public mirror of `main`, and work-in-progress branches never leave the laptop.

## ADDED Requirements

### Requirement: Repository hosted on the git server
The in-cluster git server SHALL host this repository with its full history. It SHALL be
reachable for clone, fetch and push over HTTPS at `gitea.sdlc.internal`.

#### Scenario: Clone over HTTPS
- **WHEN** a client on the host clones the repository from `https://gitea.sdlc.internal`
- **THEN** the clone succeeds without certificate errors and contains the full history of `main`

#### Scenario: Repository survives a reboot
- **WHEN** the host is rebooted
- **THEN** the repository on the git server still contains every commit pushed before the reboot

### Requirement: Origin moves to the git server
After a successful bootstrap, the `origin` remote of the local clone SHALL point to the git
server. A previous `origin` that pointed elsewhere SHALL be kept under another remote name, so
nothing it references is lost. All local branches and tags SHALL be present on the git server.

#### Scenario: Origin after bootstrap
- **WHEN** the bootstrap command has completed in a clone whose `origin` pointed to a public hosting service
- **THEN** `origin` points to the repository on `gitea.sdlc.internal`
- **AND** the previous remote is still available under the name `github`
- **AND** a plain `git push` sends commits to the git server

#### Scenario: History is preserved
- **WHEN** origin has been moved to the git server
- **THEN** every local branch and tag exists on the git server with the same commits

#### Scenario: Re-run after origin has moved
- **WHEN** the bootstrap command runs in a clone whose `origin` already points to the git server
- **THEN** the remotes are left unchanged

### Requirement: Public mirror publishes only `main`
When the public mirror is enabled, only the `main` branch SHALL be published to it. No other
branch and no tag SHALL leave the git server through the mirror.

#### Scenario: Main is mirrored
- **WHEN** a commit lands on `main` on the git server while the mirror is enabled
- **THEN** the public mirror's `main` matches it within 10 minutes

#### Scenario: Other branches stay private
- **WHEN** a commit is pushed to any branch other than `main`
- **THEN** that branch does not appear on the public mirror

### Requirement: Mirror is optional and one-way
The public mirror SHALL be enabled or disabled by a single change in the repository. Nothing
SHALL flow back from the mirror into the git server.

#### Scenario: Mirror disabled
- **WHEN** the mirror is disabled in the repository and the change lands on `main`
- **THEN** later commits on `main` are no longer published to the public mirror

#### Scenario: Changes on the mirror are ignored
- **WHEN** a commit or pull request is created on the public mirror
- **THEN** the repository on the git server is not changed by it
