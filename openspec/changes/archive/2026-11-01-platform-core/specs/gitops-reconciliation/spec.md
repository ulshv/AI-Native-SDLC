# Spec Delta

## Purpose

The platform is described in git, and the cluster keeps itself in line with that description. To change anything, you push a commit, and manual edits in the cluster are undone automatically. This keeps every platform change reviewable and repeatable.

## ADDED Requirements

### Requirement: Git is the source of platform state
Every platform component and its configuration SHALL be declared in this repository. A change
that lands on `main` in the git server SHALL be applied to the cluster without anyone running
cluster commands by hand.

#### Scenario: Configuration change applied from git
- **WHEN** a change to a platform component's configuration is pushed to `main` on the git server
- **THEN** the cluster reflects that change within 5 minutes without manual commands

### Requirement: Drift correction
Manual changes to resources managed by reconciliation SHALL be reverted to the state declared
in git.

#### Scenario: Manual edit is reverted
- **WHEN** someone changes a managed resource directly in the cluster
- **THEN** the resource returns to its declared state within 5 minutes

### Requirement: Dependency-ordered convergence
Components SHALL be applied in dependency order, so that reconciling an empty cluster converges
to a healthy platform without manual retries or intervention.

#### Scenario: Converging from an empty cluster
- **WHEN** reconciliation starts on a cluster that has only the GitOps controller and the seeded secrets
- **THEN** all declared components reach a healthy state without manual retries

### Requirement: Self-managed controller
The GitOps controller's own installation and configuration SHALL be declared in git and
reconciled like any other component.

#### Scenario: Controller setting changed through git
- **WHEN** a setting of the GitOps controller is changed in git and pushed to `main`
- **THEN** the controller applies the new setting to itself

### Requirement: Pinned versions
Every component SHALL be deployed at an explicitly pinned version. Upgrading a component SHALL
be a change in git and nothing else.

#### Scenario: No floating versions
- **WHEN** the declared platform state is inspected
- **THEN** every component references an exact version, and none uses a moving tag such as `latest`

#### Scenario: Upgrade through a commit
- **WHEN** a component's pinned version is raised in git and pushed to `main`
- **THEN** the cluster runs the new version after reconciliation
