# Spec Delta

## Purpose

Every push to a product is tested and, when the tests pass, built into an image that the environments run, and the platform's own components are built and rolled out the same way, on a runner of their own. Builds run in an isolated place without special privileges, because they execute code the agent wrote.

## ADDED Requirements

### Requirement: Every push is built and tested
Every push to a target project's repository SHALL run its tests and, when they pass, build its
container image tagged with the commit and store it in the git server's registry. The result
SHALL be shown on the commit and on its pull request.

#### Scenario: Push to a feature branch
- **WHEN** the agent pushes a commit to `feat/EWA-12_<slug>`
- **THEN** the tests run, the image `<registry>/sdlc/example-webapp:<commit>` exists afterwards, and the pull request shows the check as passed

#### Scenario: Failing tests
- **WHEN** a pushed commit breaks a test
- **THEN** no image is stored for it, and the pull request shows the check as failed

### Requirement: The platform's own images are built and deployed the same way
Every push to `main` of this repository SHALL build the images of the platform's own components,
such as the orchestrator and the agent runs, tagged with the commit, and the cluster SHALL run them
through GitOps without a manual step. These builds SHALL run on a runner that takes jobs only from
this repository, never on the runner that builds target projects.

#### Scenario: Orchestrator change reaches the cluster
- **WHEN** a commit that changes the orchestrator is pushed to `main`
- **THEN** within 10 minutes the orchestrator runs the image tagged with that commit, and no one ran a build or deploy command by hand

#### Scenario: Failed platform build
- **WHEN** the build of a commit on `main` fails
- **THEN** the orchestrator keeps running the previous image, and the failed run is visible in the git server

#### Scenario: Target project jobs stay off the platform's runner
- **WHEN** a workflow of a target project runs
- **THEN** it runs on the target projects' runner, and the platform's runner does not pick it up

### Requirement: Builds run without elevated privileges
Builds SHALL run in their own namespace, without privileged containers and without access to the
host's container runtime. Their only credential SHALL be one that lets them write to the
registry and to the project's releases.

#### Scenario: Build tries the host runtime
- **WHEN** a build step tries to reach the host's container runtime socket
- **THEN** it cannot, because no such socket is mounted

### Requirement: Builds are reported, not gating previews yet
In this version, a failed build SHALL be visible on the pull request but SHALL NOT stop the
preview from being requested. A preview whose image was never built SHALL simply not start.

#### Scenario: Preview with a failed build
- **WHEN** the implementation run finishes but the build of its commit fails
- **THEN** the pull request shows the failure, and the preview does not serve that commit
