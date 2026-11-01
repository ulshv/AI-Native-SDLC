# source-hosting Specification

## Purpose

This repository lives on the platform's own git server, which becomes the default place to push. The original GitHub copy stays, at most, as a public mirror of `main` that the operator updates by hand, and a check on that push keeps work-in-progress branches and secret values on the laptop.

## Requirements

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
Publishing to the public mirror SHALL be a manual push of `main` by the operator. A push of any
other branch, or of any tag, to the public mirror SHALL be refused before anything is sent.

#### Scenario: Main is published
- **WHEN** the operator pushes `main` to the public mirror
- **THEN** the public mirror's `main` matches the local `main`

#### Scenario: Other branches and tags stay private
- **WHEN** the operator pushes a branch other than `main`, or a tag, to the public mirror
- **THEN** the push is refused, and that ref does not appear on the public mirror

### Requirement: No secret value reaches the public mirror
A push to the public mirror SHALL be refused when any commit being pushed contains a value from
the secrets directory. The refusal SHALL name the file and SHALL NOT print the value.

#### Scenario: Secret value in a pushed commit
- **WHEN** a commit that would be pushed to the public mirror contains a value from the secrets directory
- **THEN** the push is refused, and the message names the file but not the value

### Requirement: Mirror is optional and one-way
The public mirror SHALL receive commits only when the operator pushes them. Nothing SHALL flow
back from the mirror into the git server.

#### Scenario: Changes on the mirror are ignored
- **WHEN** a commit or pull request is created on the public mirror
- **THEN** the repository on the git server is not changed by it
