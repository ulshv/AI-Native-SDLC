# Spec Delta

## Purpose

A ticket handed to the agent moves on its own from a spec to code to a release, and it always shows in the tracker where it stands. People stay in charge at two points: nothing is built before a person approves the spec, and nothing is merged without a person. The orchestrator keeps track of every item, so a restart or a repeated notification never loses or doubles work.

## ADDED Requirements

### Requirement: Intake by assignment
The orchestrator SHALL start work on a ticket of a connected tracker project when the ticket is
assigned to `@agent`, and SHALL not start work on tickets that are not assigned to it.

#### Scenario: Ticket assigned to the agent
- **WHEN** a person assigns ticket EWA-12 of a connected project to `@agent`
- **THEN** within 1 minute the ticket moves to the Drafting status and the agent starts drafting its spec

#### Scenario: Ticket not assigned to the agent
- **WHEN** a ticket is created or changed without `@agent` among its assignees
- **THEN** the orchestrator takes no action on it and writes nothing to it

### Requirement: Phase mirrored in the tracker
Each work item SHALL have one current phase, kept by the orchestrator, and the ticket's status in
the tracker SHALL show it: Drafting, Needs info, Spec review, Implementing, Deploying to QA,
Code review, On dev, Released, or Closed.

#### Scenario: Status follows the phase
- **WHEN** a work item moves from one phase to another
- **THEN** within 1 minute the ticket's status in the tracker shows the new phase

### Requirement: Spec drafted as a pull request
In the Drafting phase, the agent SHALL write an OpenSpec change for the ticket on the branch
`feat/<TICKET-ID>_<slug>`, branched off `development`, and open one pull request into
`development` that holds only that change. The ticket SHALL get a comment with a link to it.

#### Scenario: Spec ready for review
- **WHEN** drafting of EWA-12 finishes without open questions
- **THEN** a pull request from `feat/EWA-12_<slug>` into `development` exists with only files under `openspec/changes/`
- **AND** the ticket is in Spec review and has a comment linking the pull request

### Requirement: Clarifying questions stop the work
When the agent lacks information to write the spec or the code, it SHALL post its questions as a
comment in the ticket, move the item to Needs info, and stop. The next comment that mentions
`@agent` SHALL resume the work with all answers given so far.

#### Scenario: Agent asks and waits
- **WHEN** the agent cannot tell from the ticket whether a filter should survive a page reload
- **THEN** the ticket gets a comment with the question, its status is Needs info, and no agent run is active for it

#### Scenario: Answer resumes the work
- **WHEN** a person answers in a comment that mentions `@agent` while the item is in Needs info
- **THEN** the item returns to the phase it was in, and the next agent run receives the question and the answer

### Requirement: Spec approval gate
No code SHALL be written for a work item until its spec is approved by a person allowed to
approve specs. Approval SHALL start the implementation in the same pull request.

#### Scenario: Spec approved
- **WHEN** an allowed person approves the spec of an item in Spec review
- **THEN** the item moves to Implementing and an agent run implements the change on the same branch

#### Scenario: Changes requested on the spec
- **WHEN** an allowed person requests changes to the spec, with the requested changes in the comment
- **THEN** the item moves to Drafting and the next agent run revises the spec on the same branch using that comment

### Requirement: Implementation lands in the same pull request
In the Implementing phase, the agent SHALL implement the approved change and push the code to
the same branch, leaving the OpenSpec change unarchived. The item SHALL then move to Deploying to
QA, or straight to Code review when the project has previews turned off.

#### Scenario: Code pushed
- **WHEN** the implementation run for EWA-12 finishes successfully in a project with previews
- **THEN** the pull request contains the code and the still unarchived change, and the item is in Deploying to QA

#### Scenario: Changes requested on the code
- **WHEN** an allowed person requests changes to the code
- **THEN** the item moves to Implementing, the agent replies where it was asked that it is working on it, and the next run pushes a fix to the same branch
- **AND** when the preview serves the fix, the agent replies in the same place that it is ready, with the preview link

### Requirement: Code review starts on a running QA stand
In Deploying to QA, the item SHALL wait until its preview serves the pushed commit, then move to
Code review with a comment linking the preview. If the preview does not serve it within 15
minutes, the item SHALL move to Code review with a comment that says why the preview is missing
and links the failed build or deployment.

#### Scenario: Preview is up
- **WHEN** the preview of EWA-12 starts serving the pushed commit
- **THEN** within 1 minute the item is in Code review and the ticket has a comment with `https://ewa-12.qa.example-webapp.sdlc.internal`

#### Scenario: Preview does not come up
- **WHEN** the build of the pushed commit fails and no preview serves it for 15 minutes
- **THEN** the item moves to Code review, and the ticket has a comment that the preview is unavailable, with a link to the failed build

### Requirement: Change archived after the code is approved
When an allowed person approves the code, the agent SHALL push one more commit to the same branch
that archives the OpenSpec change, merging its delta into the project's specs, and SHALL then say
in the pull request that it is ready to merge. A request to merge counts as an approval.

#### Scenario: Code approved
- **WHEN** an engineer approves the pull request of EWA-12 in Code review
- **THEN** the branch gains one commit that archives the change and updates the project's specs, and the pull request gets a comment that it is ready to merge

#### Scenario: Archive and merge in one request
- **WHEN** an engineer writes "@agent ok, merge it into dev" before the change is archived
- **THEN** the agent first pushes the archive commit, then merges

### Requirement: Code review gate
No change SHALL reach `development` without a person: either the person merges the pull request,
or a person allowed to merge asks `@agent` to merge it. After the merge the item SHALL be On dev.

#### Scenario: Merge on request
- **WHEN** an engineer writes "@agent ok, merge it into dev" on an item in Code review
- **THEN** the archive commit is on the branch, the pull request is merged into `development`, and the item moves to On dev

#### Scenario: Person merges in the git server
- **WHEN** a person merges the pull request in the git server's web interface after the archive commit
- **THEN** the item moves to On dev without any further action

#### Scenario: Merged before the archive commit
- **WHEN** a person merges pull request #34 of EWA-12 before the archive commit was pushed
- **THEN** the item moves to On dev, and the ticket gets a comment that the specs in pull request #34 on EWA-12 were not archived and asking for an archive pull request

#### Scenario: Archive pull request on request
- **WHEN** an engineer answers that comment with "@agent ok, create archive PR"
- **THEN** the agent opens a pull request into `development` with only the commit that archives the change, and replies with its link

### Requirement: Rejection closes the item
A product owner or an engineer of the project SHALL be able to reject a work item in any open
phase. A rejected item SHALL be Closed, and its pull request closed without merging.

#### Scenario: Spec rejected
- **WHEN** an allowed person rejects an item in Spec review
- **THEN** the item is Closed, its pull request is closed unmerged, and no further agent run starts for it

### Requirement: Conversations are threads
The agent's work SHALL happen in threads, each with its own id. A thread SHALL be linked to the
outside objects it works on, such as a tracker work item, a pull request or a chat thread, and
each object SHALL be linked to one thread. All conversations about one work item, its ticket's
comments and its pull request's reviews, SHALL go to the same thread.

#### Scenario: Ticket and pull request in one thread
- **WHEN** a person comments on ticket EWA-12 and later reviews its pull request
- **THEN** both reach the same thread, and the agent's answer to the review takes the ticket's discussion into account

#### Scenario: Looking up a thread
- **WHEN** an operator opens a thread by its id in the orchestrator
- **THEN** they see its messages, its phase, branch, change and preview address, and the objects linked to it

#### Scenario: Finding a thread by a linked object
- **WHEN** an operator looks up the thread linked to pull request `sdlc/example-webapp#34`
- **THEN** they get the thread that works on EWA-12

### Requirement: The core does not depend on one tracker
Tickets, pull requests and conversations SHALL reach the orchestrator through adapters. Its
threads, links and policy SHALL NOT depend on a particular tracker or git server, so another
source, such as another tracker, a chat or a tool reached through MCP, can be added as an adapter.
A work item's human-readable key SHALL come from its source.

#### Scenario: Key from the source
- **WHEN** a work item comes from Plane project `EWA` as its twelfth work item
- **THEN** its key is `EWA-12`, and its branch and preview names are derived from that key

### Requirement: Events are processed once and in order
Every incoming event SHALL be recorded before it is acted on. An event delivered twice SHALL take
effect once, and events for the same thread SHALL be applied one at a time in the order they
were received.

#### Scenario: Duplicate delivery
- **WHEN** the tracker delivers the same webhook twice
- **THEN** the second delivery is recorded as a duplicate and changes nothing

#### Scenario: Events arrive while a thread is busy
- **WHEN** two comments for the same thread arrive while an earlier event for it is still being handled
- **THEN** they are applied after it, one at a time, in the order they arrived

### Requirement: Restart loses nothing
Restarting the orchestrator SHALL NOT lose a work item's phase, its waiting state or an event that
arrived before the restart, and SHALL NOT miss an agent run that finished while it was down.

#### Scenario: Restart while waiting for a person
- **WHEN** the orchestrator restarts while an item waits in Spec review
- **THEN** after the restart the item is still in Spec review, and the next approval moves it on as before

#### Scenario: Agent run finishes during a restart
- **WHEN** an agent run finishes while the orchestrator is not running
- **THEN** after the orchestrator starts, the item moves on as if it had been running
