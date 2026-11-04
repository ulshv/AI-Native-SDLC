# Spec Delta

## ADDED Requirements

### Requirement: Container images hosted on the git server
The git server SHALL store container images for the platform and its target projects, under the
same owner as their repositories. The cluster SHALL pull those images without per-namespace
setup, and images SHALL survive a reboot.

#### Scenario: Cluster pulls a stored image
- **WHEN** an image is pushed to the git server's registry and a pod in any namespace references it
- **THEN** the pod starts with that image, with no pull secret declared in its namespace

#### Scenario: Image survives a reboot
- **WHEN** the host is rebooted
- **THEN** every image pushed before the reboot can still be pulled

### Requirement: Service accounts for the agent
The git server SHALL have two service accounts for the loop: `agent`, used by the orchestrator to
comment, open pull requests and merge where allowed, and `agent-runner`, used by agent runs to push
feature branches. Their tokens SHALL be created by the bootstrap and SHALL never be written to git.

#### Scenario: Accounts after bootstrap
- **WHEN** the bootstrap command has completed
- **THEN** both accounts exist, and each one's token is available only to the component that uses it
