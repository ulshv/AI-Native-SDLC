# Spec Delta

## Purpose

Every change can be tried in a running environment before it is merged, and merged work reaches a shared dev environment and, after a release, production. Each environment runs exactly the commit it claims to, and it is all declared in git like the rest of the platform.

## ADDED Requirements

### Requirement: Preview per pull request
When the implementation of a work item is pushed, its pull request SHALL get a preview
environment at `<identifier>-<number>.qa.<project domain>`, where the identifier and the number
are the tracker project's and the work item's, in lower case, for example
`ewa-12.qa.example-webapp.sdlc.internal`, running the pull request's latest commit.

#### Scenario: Preview after implementation
- **WHEN** the implementation run for EWA-12 finishes and the image for its commit exists
- **THEN** within 5 minutes `https://ewa-12.qa.example-webapp.sdlc.internal` serves the app built from that commit

#### Scenario: Preview follows new commits
- **WHEN** a fix is pushed to the same pull request
- **THEN** the preview serves the new commit within 5 minutes of its image existing

### Requirement: Preview removed with its pull request
A preview SHALL exist only while its pull request is open. Merging or closing the pull request
SHALL remove the preview and everything it runs.

#### Scenario: Pull request merged
- **WHEN** the pull request of EWA-12 is merged
- **THEN** within 5 minutes the preview no longer runs and its address no longer serves the app

### Requirement: Dev follows development
`dev.<project>.sdlc.internal` SHALL run the latest commit of `development` after every merge into
it.

#### Scenario: Merge reaches dev
- **WHEN** a pull request is merged into `development`
- **THEN** within 5 minutes of the image existing, `https://dev.example-webapp.sdlc.internal` serves that commit

### Requirement: Production follows main
`<project>.sdlc.internal` SHALL run the latest commit of `main`, which changes only through a
release.

#### Scenario: Release reaches production
- **WHEN** a release pull request is merged into `main`
- **THEN** within 5 minutes of the image existing, `https://example-webapp.sdlc.internal` serves that commit

### Requirement: The image tag is the commit
Every environment SHALL run the image tagged with the exact commit it deploys. No automated
commit SHALL be needed to change which image an environment runs.

#### Scenario: Which commit runs where
- **WHEN** an operator inspects the dev environment
- **THEN** its image tag equals the commit at the head of `development` that the environment last synced

### Requirement: Environments are declared in git
The preview, dev and production environments SHALL be declared in git and reconciled like the
rest of the platform; a manual change to them SHALL be reverted.

#### Scenario: Manual change to dev
- **WHEN** someone scales the dev deployment by hand
- **THEN** it returns to its declared state within 5 minutes
