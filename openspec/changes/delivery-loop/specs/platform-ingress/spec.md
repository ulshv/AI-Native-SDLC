# Spec Delta

## ADDED Requirements

### Requirement: Names created on the fly
Any name under `sdlc.internal`, at any depth, SHALL resolve from the host and from pods without
editing the declared list or re-running the bootstrap, so environments can appear and disappear
on their own. This SHALL keep working offline and with the host's VPN client running.

#### Scenario: New preview name
- **WHEN** a preview for EWA-12 starts at `ewa-12.qa.example-webapp.sdlc.internal`
- **THEN** that name resolves and is reachable from a browser on the host and from pods, with no change to the declared list

#### Scenario: VPN client running
- **WHEN** the host's VPN client is connected and a preview name is looked up on the host
- **THEN** it resolves to the platform, not through the VPN's DNS

### Requirement: Certificates for environment names
Every environment name of a target project, such as `dev.example-webapp.sdlc.internal`,
`example-webapp.sdlc.internal` and `ewa-12.qa.example-webapp.sdlc.internal`, SHALL be served over
HTTPS with a certificate that chains to the local root CA.

#### Scenario: Preview over HTTPS
- **WHEN** a browser on the host opens a preview address
- **THEN** it loads without certificate warnings
