# Spec Delta

## Purpose

A product joins the loop by adding one configuration entry: it keeps its own repository, its own tracker project and its own specs. Its main branches are protected by the git server itself. The first product is `example-webapp`, a small app built to demonstrate the loop.

## ADDED Requirements

### Requirement: A project is connected by configuration
A target project SHALL be connected to the loop by one directory of configuration in this
repository, whose main file names its tracker project, its git repository, its people with their
roles, and its environment names. Connecting another project SHALL need no code change.

#### Scenario: Project connected
- **WHEN** a directory for project `EWA` and repository `sdlc/example-webapp` is added and reconciled
- **THEN** tickets of `EWA` assigned to `@agent` start work in `sdlc/example-webapp`

#### Scenario: Unconnected project
- **WHEN** a ticket in a tracker project without such a directory is assigned to `@agent`
- **THEN** the orchestrator takes no action on it

### Requirement: Each project in its own repository
Each target project SHALL live in its own git repository on the git server, separate from this
repository, with its own OpenSpec specs that describe the product.

#### Scenario: Agent works in the product's repository
- **WHEN** the agent drafts a spec for a ticket of `EWA`
- **THEN** the change is written in `sdlc/example-webapp` against that repository's specs, and this repository is not touched

### Requirement: Protected long-lived branches
A target project SHALL have `development` and `main` branches that nobody can push to directly.
`development` SHALL accept merges from people with the engineer role and from the orchestrator's
account; `main` SHALL accept merges only from people on the project's merge allowlist, after an
approving review by a person.

#### Scenario: Orchestrator merges into main
- **WHEN** the orchestrator's account tries to merge a pull request into `main`
- **THEN** the git server refuses it

#### Scenario: Engineer merges a release
- **WHEN** an engineer on the allowlist merges an approved release pull request into `main`
- **THEN** the merge succeeds

### Requirement: Repositories seeded from local clones
A seeding command, separate from the platform's bootstrap, SHALL create each connected target
project's repository on the git server from that project's own local clone when the repository is
missing, with all branches and tags and with its branch protection. The location of each clone
SHALL be configurable, with `~/Projects/sdlc/example-webapp` as the default for `example-webapp`.

#### Scenario: Seeding after a rebuild
- **WHEN** the platform has been rebuilt and the operator runs the seeding command with `~/Projects/sdlc/example-webapp` present
- **THEN** `sdlc/example-webapp` on the git server has the same `main` and `development` as the clone, with branch protection in place

#### Scenario: Repository already present
- **WHEN** the seeding command runs and `sdlc/example-webapp` already exists on the git server
- **THEN** its contents are left unchanged

#### Scenario: Local clone missing
- **WHEN** the seeding command runs and the configured clone of a target project does not exist
- **THEN** it names the missing clone, exits non-zero, and changes nothing on the git server

### Requirement: The example project
The first target project SHALL be `example-webapp`: a React web app with a login form that
accepts a demo account and a dashboard of orders built on mocked data, with automated tests and
OpenSpec specs for login and the dashboard. It SHALL be tracked as project `EWA`.

#### Scenario: Demo login
- **WHEN** a user opens the app and signs in with the demo account
- **THEN** the dashboard shows a list of orders from the mocked data

#### Scenario: Tests run
- **WHEN** the app's test command runs on a fresh clone
- **THEN** all tests pass
