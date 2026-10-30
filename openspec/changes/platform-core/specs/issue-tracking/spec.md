# Spec Delta

## Purpose

The platform has its own issue tracker for this project's work items, and it is ready right after bootstrap with its admin account already set up. People use it in the browser, and automated tools can read and update tickets and get notified about changes. The delivery loop will rely on that later.

## ADDED Requirements

### Requirement: Tracker available
The issue tracker SHALL be reachable over HTTPS at `plane.sdlc.internal`. Users SHALL sign in
with local accounts, because single sign-on is out of scope for this change.

#### Scenario: Sign in and create a ticket
- **WHEN** a user with a local account signs in at `https://plane.sdlc.internal` and creates a work item in a project
- **THEN** the work item is saved and visible in that project

### Requirement: Instance admin provisioned automatically
After bootstrap, the tracker SHALL already have an instance administrator whose credentials come
from the secrets directory. No first-run setup screen SHALL remain open for anyone to claim the
instance.

#### Scenario: Admin exists after bootstrap
- **WHEN** the bootstrap command has completed on a fresh host
- **THEN** the operator signs in to the tracker's instance administration with the email and password from the secrets directory
- **AND** the first-run setup screen does not offer to create a new instance administrator

#### Scenario: Admin provisioning on re-run
- **WHEN** the bootstrap command or a reconciliation runs again after the admin exists
- **THEN** the admin account and its password are left unchanged

### Requirement: Tracker data persists
Work items, projects and accounts SHALL persist across pod restarts and host reboots.

#### Scenario: Ticket survives a reboot
- **WHEN** the host is rebooted after a work item was created
- **THEN** the work item is still present with its status and comments

### Requirement: API access for automated clients
The tracker SHALL let an automated client authenticate with an API key and read and change work
items, including their status and comments.

#### Scenario: Reading and updating a work item through the API
- **WHEN** a client with a valid API key requests a work item and then changes its status
- **THEN** the request succeeds, and the new status is visible in the tracker's web interface

### Requirement: Webhooks reach in-cluster services
The tracker SHALL deliver webhooks for work item changes to an endpoint served from inside the
cluster and addressed by its in-cluster service name.

#### Scenario: Status change triggers a webhook
- **WHEN** a webhook is configured for an in-cluster endpoint and a work item's status changes
- **THEN** the endpoint receives a webhook describing the change within 1 minute
