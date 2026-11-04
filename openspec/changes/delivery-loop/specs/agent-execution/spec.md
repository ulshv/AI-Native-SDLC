# Spec Delta

## Purpose

The agent does its work in short, isolated runs that start clean, reach only what they need and cannot merge anything. Which model they use is a setting, and every run ends with a clear result, so the orchestrator always knows what happened.

## ADDED Requirements

### Requirement: Every agent run is an isolated job
Each agent run SHALL execute as its own short-lived job in the cluster, in a namespace reserved
for agent runs, starting from a fresh copy of the target repository. Nothing SHALL be kept from
one run to the next except what was pushed to the git server.

#### Scenario: Two runs for the same item
- **WHEN** the agent drafts a spec and later implements it
- **THEN** each step runs in a separate job, and the second job starts from what the first one pushed

### Requirement: Agent runs cannot merge
Agent runs SHALL push with a git server account that can push only to feature branches and is
allowed to merge into no branch. Agent runs SHALL receive no tracker credentials and no
production secrets.

#### Scenario: Run tries to merge
- **WHEN** code inside an agent run calls the git server to merge a pull request
- **THEN** the git server refuses the merge

#### Scenario: Run tries to push to development
- **WHEN** an agent run pushes directly to `development` or `main`
- **THEN** the git server rejects the push

### Requirement: Limited network reach
Agent runs SHALL reach only the cluster's DNS, the git server and the outside internet for the
model provider. They SHALL NOT reach other platform services such as the tracker, the database
or the orchestrator.

#### Scenario: Run tries the database
- **WHEN** an agent run opens a connection to the shared database
- **THEN** the connection is blocked

### Requirement: Models are configuration
The model provider and the model used by agent runs SHALL be configuration of the orchestrator,
and any provider with an OpenAI-compatible API SHALL be usable. Changing them SHALL need no new
image. The configuration in this repository starts with DeepSeek V4 Flash through RouterAI.

#### Scenario: Switching the provider
- **WHEN** the operator points the configuration at another OpenAI-compatible provider, with its key in the secrets directory
- **THEN** the next agent run uses that provider, with the same image

#### Scenario: Switching the model
- **WHEN** the operator changes the configured model in git and the change is reconciled
- **THEN** the next agent run uses the new model, with the same image

### Requirement: A run reports a structured result
Every agent run SHALL end with one result the orchestrator can read without parsing logs:
`spec_ready`, `code_ready`, `archived`, `questions` with the list of questions, or `failed` with a reason,
plus the commit it pushed if any.

#### Scenario: Run needs answers
- **WHEN** an agent run finds that the ticket lacks information
- **THEN** it pushes nothing and ends with `questions` and the list of questions

#### Scenario: Run fails
- **WHEN** an agent run crashes or exceeds its time limit
- **THEN** the orchestrator receives `failed` with a reason, and the ticket gets a comment saying the run failed

### Requirement: Runs are bounded
An agent run SHALL stop after 30 minutes. No more than two agent runs SHALL be active at the
same time; further runs SHALL wait their turn.

#### Scenario: Third run requested
- **WHEN** two agent runs are active and a third item needs a run
- **THEN** the third run starts only after one of the two finishes
