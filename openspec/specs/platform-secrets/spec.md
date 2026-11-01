# platform-secrets Specification

## Purpose

Passwords, keys and tokens never go into git, so a public copy of the repository cannot leak them. They live in a folder outside the repository, are generated automatically where possible, and stay the same across rebuilds until you deliberately change them.

## Requirements

### Requirement: Secret values never enter git
No secret value SHALL be committed to this repository. The repository SHALL contain only the
list of required secret names, with a description of each and an indication of whether it is
generated or supplied externally.

#### Scenario: Repository holds names only
- **WHEN** the repository's tracked files are inspected
- **THEN** they contain the list of required secret names and no secret values

#### Scenario: Accidental secret file in the working tree
- **WHEN** a secrets file or private key file is placed inside the working tree by mistake
- **THEN** git ignores it, and it does not appear as a file to commit

### Requirement: Secrets stored outside the repository
Secret values SHALL be read from a secrets directory outside the repository's working tree, so
tools and agents that operate inside the working tree cannot read them.

#### Scenario: Working tree contains no secret material
- **WHEN** the bootstrap has completed and every file in the working tree is listed
- **THEN** no file in the working tree contains a secret value

### Requirement: Generated on first use
Secrets that can be generated, such as database passwords and session keys, SHALL be generated
on the first bootstrap and written to the secrets directory. Secrets issued by external parties
SHALL be supplied by the operator.

#### Scenario: First run generates missing secrets
- **WHEN** the bootstrap command runs and a generatable secret is absent from the secrets directory
- **THEN** a strong random value is generated, stored in the secrets directory and used by the platform

#### Scenario: External secret is not generated
- **WHEN** a secret that must come from an external party is absent
- **THEN** the bootstrap command does not invent a value and reports the missing secret

### Requirement: Values persist across rebuilds
A cluster rebuild SHALL reuse the secret values already stored in the secrets directory.

#### Scenario: Same credentials after a rebuild
- **WHEN** the cluster is rebuilt with the same secrets directory
- **THEN** every service uses the same secret values as before the rebuild

### Requirement: Rotation
Changing a value in the secrets directory and re-running the bootstrap command SHALL update the
value inside the cluster. Services that consume it SHALL use the new value after they restart.

#### Scenario: Rotating a secret
- **WHEN** the operator changes a secret value in the secrets directory and re-runs the bootstrap command
- **THEN** the cluster holds the new value, and the consuming service uses it after a restart
