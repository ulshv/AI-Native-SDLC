# Spec Delta

## ADDED Requirements

### Requirement: Webhooks for comments and assignment
The tracker SHALL deliver webhooks to in-cluster endpoints when a work item's assignees change and
when a comment is added to it, including who made the change and the comment's text and mentions.

#### Scenario: Comment triggers a webhook
- **WHEN** a person adds a comment that mentions `@agent` to a work item of a project with a webhook
- **THEN** the endpoint receives a webhook within 1 minute that carries the comment text, its author and the mention

#### Scenario: Assignment triggers a webhook
- **WHEN** a work item is assigned to `@agent`
- **THEN** the endpoint receives a webhook within 1 minute that names the new assignee

### Requirement: Agent account in the tracker
The tracker SHALL have a member account `agent`, which people can assign work items to and mention
in comments, and which the orchestrator uses through an API key.

#### Scenario: Assigning the agent
- **WHEN** a person opens a work item in project `EWA`
- **THEN** `agent` is offered as an assignee and as a mention

### Requirement: Statuses of the loop
Each connected tracker project SHALL have the statuses the loop uses: Drafting, Needs info,
Spec review, Approved, Implementing, Deploying to QA, Code review, On dev, Released and Closed.

#### Scenario: Project ready for the loop
- **WHEN** a person opens the status list of project `EWA`
- **THEN** every status of the loop is available
