# Spec Delta

## Purpose

People talk to the agent in plain words, in the tools they already use, by mentioning `@agent`. A model only works out what they asked for; fixed rules decide whether that person may ask for it right now, so a misread comment can cost a question, never an action nobody allowed.

## ADDED Requirements

### Requirement: One agent identity in every tool
The agent SHALL appear to people as `@agent`, with a service account of that name in the tracker
and in the git server. Comments, statuses and pull requests the agent writes SHALL come from
those accounts.

#### Scenario: Agent's comment in the tracker
- **WHEN** the agent comments on a ticket
- **THEN** the comment is shown as written by `@agent`

### Requirement: Only mentions are read
The agent SHALL act only on comments that mention `@agent`, in a connected project's tickets or
in its pull requests. Comments without the mention SHALL NOT trigger anything.

#### Scenario: Comment between people
- **WHEN** two people discuss a ticket in comments that do not mention `@agent`
- **THEN** the agent does nothing and writes nothing

#### Scenario: Mention in a pull request
- **WHEN** a person writes a review comment that mentions `@agent` on a pull request of a connected project
- **THEN** the agent handles it the same way as a mention in the ticket of that pull request

### Requirement: Plain words become an intent
A comment that mentions `@agent` SHALL be read as one intent: `approve`, `request_changes`,
`reject`, `answer`, `merge`, `archive`, `prepare_release`, `ask` (a question that asks for no
action), or none. A comment the agent cannot read confidently SHALL get a clarifying reply
instead of an action.

#### Scenario: Approval in plain words
- **WHEN** a product owner writes "@agent looks good, go ahead" on an item in Spec review
- **THEN** the agent treats it as `approve`

#### Scenario: Unclear comment
- **WHEN** a comment mentions `@agent` but its intent is ambiguous, such as "@agent hmm, maybe"
- **THEN** the agent replies with a question about what is meant, naming what that person may ask for in the item's phase, and changes nothing else

#### Scenario: Question about the item
- **WHEN** a person of the project writes "@agent what is the status of this ticket?" on an item On dev
- **THEN** the agent answers from the item's phase, pull request and what may be asked next, and changes nothing

#### Scenario: Question while the agent waits for answers
- **WHEN** an item is in Needs info and a person writes "@agent why are you waiting?"
- **THEN** the agent answers with the questions it waits on, and the item stays in Needs info

#### Scenario: Rejection while the agent waits for answers
- **WHEN** an item is in Needs info and a person allowed to reject writes "@agent reject it"
- **THEN** the item is closed and no agent run starts, rather than the comment being taken as the answer

### Requirement: Explicit signals need no interpretation
Explicit signals SHALL count as intents without a model reading them: setting a ticket's status
to Approved in Spec review means `approve`, setting it to Closed means `reject`, and an approving
or change-requesting review in the git server, on the spec or on the code, means `approve` or
`request_changes`.

#### Scenario: Status set to Approved
- **WHEN** a product owner sets the status of an item in Spec review to Approved
- **THEN** the agent treats it as `approve` without reading any comment

### Requirement: Code decides what is allowed
Whether an intent is carried out SHALL be decided by a fixed policy over who asked, their role
and the item's phase, never by the model. A request that the policy refuses SHALL get a reply
that says why, and SHALL change nothing.

#### Scenario: Engineer asks to merge
- **WHEN** an engineer asks `@agent` to merge an item in Code review
- **THEN** the merge happens

#### Scenario: Person without the role asks to merge
- **WHEN** a person who is not an engineer of the project asks `@agent` to merge
- **THEN** the agent replies that this person cannot merge, and nothing is merged

#### Scenario: Intent that does not fit the phase
- **WHEN** someone asks `@agent` to merge an item that is still in Spec review
- **THEN** the agent replies that the item is not ready to merge, and its phase does not change

### Requirement: People are known across tools
The orchestrator SHALL know which tracker account and which git server account belong to the
same person, and that person's roles in each connected project. Requests from unknown accounts
SHALL be refused with a reply.

#### Scenario: Same person in two tools
- **WHEN** a product owner approves in the tracker and later comments in the pull request
- **THEN** both requests are attributed to the same person with the same roles

### Requirement: The agent answers where it was asked
The agent SHALL reply in the place the request came from: in the ticket for a tracker comment,
and in the pull request for a git server comment or review.

#### Scenario: Request from a pull request review
- **WHEN** an engineer asks for changes in a review in the git server
- **THEN** the agent's replies about that request appear in the same pull request

### Requirement: No echo loops
Events caused by the agent's own accounts SHALL be ignored, so the agent never reacts to its own
comments, status changes or pushes.

#### Scenario: Agent changes a status
- **WHEN** the agent sets a ticket's status and the tracker sends a webhook about it
- **THEN** the orchestrator records the event as the agent's own and takes no action
