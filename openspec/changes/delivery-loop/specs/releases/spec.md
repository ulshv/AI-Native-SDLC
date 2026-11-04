# Spec Delta

## Purpose

A release is prepared by the agent and shipped by a person. The agent collects what was merged since the last release into a release pull request; once a person merges it, the version is tagged and the released tickets are marked, so the tracker shows what reached production.

## ADDED Requirements

### Requirement: The agent prepares a release on request
When a person allowed to request releases asks `@agent` to prepare one, the agent SHALL open a
pull request from `development` into `main` titled with the next version, whose description lists
the tickets merged since the last release, and SHALL reply with its link where it was asked.

#### Scenario: Release requested in the tracker
- **WHEN** a product owner writes "@agent let's prepare a release" on a ticket of `EWA`, with EWA-9 and EWA-12 merged since v0.1.0
- **THEN** a pull request "Release 0.2.0" from `development` into `main` exists, its description lists EWA-9 and EWA-12, and the ticket gets a reply with its link

#### Scenario: Nothing to release
- **WHEN** a release is requested and `development` has no commits that `main` lacks
- **THEN** no pull request is opened, and the agent replies that there is nothing to release

### Requirement: Only a person ships a release
A release pull request SHALL be merged only by a person, after an approving review by a person.
The agent SHALL NOT merge it, even when asked.

#### Scenario: Agent asked to merge a release
- **WHEN** someone asks `@agent` to merge the release pull request
- **THEN** the agent replies that only a person can merge into `main`, and nothing is merged

### Requirement: Version and tag
Merging a release into `main` SHALL create the tag of the next minor version, for example
`v0.2.0` after `v0.1.0`, and a release in the git server with the listed tickets. A major
version SHALL be set only by hand.

#### Scenario: Release merged
- **WHEN** a person merges "Release 0.2.0" into `main`
- **THEN** tag `v0.2.0` points at the merge commit, and a release `v0.2.0` exists listing EWA-9 and EWA-12

#### Scenario: First release
- **WHEN** the first release of a project is merged and no version tag exists yet
- **THEN** the tag is `v0.1.0`

### Requirement: Released tickets are marked
When a release is published, every ticket it lists SHALL move to the Released status and get a
comment with the version.

#### Scenario: Tickets released
- **WHEN** release `v0.2.0` listing EWA-9 and EWA-12 is published
- **THEN** both tickets are Released and have a comment naming v0.2.0
