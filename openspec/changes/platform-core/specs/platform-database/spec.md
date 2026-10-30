# Spec Delta

## Purpose

All platform services share one database server instead of each running its own, which saves memory on the laptop. Each service still gets its own private database that no other service can open, and the data survives restarts.

## ADDED Requirements

### Requirement: One shared database service
Platform services that need a relational database SHALL use the shared database service instead
of running their own database instances.

#### Scenario: No per-service database instances
- **WHEN** the platform's running workloads are inspected
- **THEN** the git server and the issue tracker store their relational data in the shared database service
- **AND** neither of them runs its own relational database instance

### Requirement: Per-service isolation
Each platform service SHALL have its own database and its own credentials. A service's
credentials SHALL NOT grant access to another service's database.

#### Scenario: Credentials are scoped to one database
- **WHEN** a client connects with the git server's database credentials and tries to open the issue tracker's database
- **THEN** the connection is refused

### Requirement: Data survives restarts
Data in the shared database service SHALL persist across pod restarts and host reboots.

#### Scenario: Host reboot
- **WHEN** the host is rebooted and the platform comes back up
- **THEN** users, repository metadata and tickets created before the reboot are still present
