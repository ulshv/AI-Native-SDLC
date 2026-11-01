# Spec Delta

## Purpose

One command builds the whole platform on an empty laptop, and the same command rebuilds it after the cluster is wiped. It needs only the local clone of this repository and the secrets folder, and it is safe to run again at any time. When it finishes, git takes over and the script stays out of the way.

## ADDED Requirements

### Requirement: Single-command bootstrap
The platform SHALL be created on a host that has no cluster by running one command from the
local clone of this repository, given a populated secrets directory. No other manual step
SHALL be needed for the platform core services to become reachable.

#### Scenario: Fresh host
- **WHEN** the operator runs the bootstrap command on a host without a cluster
- **THEN** the git server, the issue tracker and the GitOps controller become reachable at their service names over HTTPS
- **AND** all of them report healthy without further manual steps

### Requirement: Idempotent re-run
Running the bootstrap command on an already bootstrapped host SHALL converge to the same state
without recreating running services and without regenerating secrets that already exist.

#### Scenario: Second run on a healthy platform
- **WHEN** the operator runs the bootstrap command again on a healthy platform
- **THEN** the command completes successfully
- **AND** no secret value changes and no service loses data

#### Scenario: Resume after a failed run
- **WHEN** a previous bootstrap run stopped midway because of an error that has since been fixed
- **THEN** running the bootstrap command again completes the setup without manual cleanup

### Requirement: Preflight checks
The bootstrap command SHALL verify its prerequisites before changing anything on the host, and
SHALL stop with a message naming each missing prerequisite, such as a required external secret
or insufficient privileges.

#### Scenario: Missing external secret
- **WHEN** a secret that cannot be generated is absent from the secrets directory
- **THEN** the bootstrap command exits with a non-zero status and names the missing secret
- **AND** nothing on the host has been changed

### Requirement: Rebuild from the local clone
After the cluster is destroyed, the bootstrap command SHALL restore the platform configuration
and the repository contents from the local clone and the secrets directory. Application data,
such as tickets and review discussions, is not restored by this capability.

#### Scenario: Rebuild after the cluster is removed
- **WHEN** the cluster is removed and the operator runs the bootstrap command
- **THEN** the git server holds the same `main` commit as the local clone
- **AND** services accept the same credentials as before the rebuild
- **AND** the host trusts the platform certificates without re-importing anything

### Requirement: Hand-over to GitOps
When the bootstrap command completes, every component it installed SHALL be managed by GitOps
reconciliation from the git server. On a cluster where hand-over has already happened, later
runs of the bootstrap command SHALL make no changes to those components.

#### Scenario: Components adopted after bootstrap
- **WHEN** the bootstrap command completes
- **THEN** every component it installed, including the GitOps controller itself, is reported as reconciled from git and in sync
