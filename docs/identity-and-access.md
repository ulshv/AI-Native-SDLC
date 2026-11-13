# Identity and access

Input for the roadmap's step 6, SSO with Keycloak ([D9](decisions.md#d9-keycloak-where-sso-is-free)):
who has which account today and where each piece lives, the model to aim at, and the questions
the SSO proposal has to settle. Written when the delivery loop got its people's accounts
(task 7.28 of `openspec/changes/delivery-loop/`), so that SSO starts from one picture instead of
a dozen files.

[← back to README](../README.md)

---

## Today

| Accounts | Where defined | Made by | Sign-in |
|---|---|---|---|
| Instance admins: `admin` (Plane), `gitea_admin` (Gitea), `admin` (Argo CD) | `PLANE_ADMIN_*`, `GITEA_ADMIN_*` in `secrets.env`; Argo CD's own `argocd-initial-admin-secret` | Plane's admin Job, Gitea's chart, Argo CD; the bootstrap sets the Gitea admin's e-mail address | a password per tool |
| Services: `agent`, `agent-runner`, `reader`, `ci`, `ci-platform` (Gitea); `agent` (Plane) | `SERVICE_ACCOUNTS` in `platform/bootstrap.sh`; `platform/apps/plane/setup.py` | the bootstrap; Plane's setup Job | tokens, only in cluster Secrets |
| People: `sergey`, `manager`, `dev` | `~/.config/ai-native-sdlc/accounts.yaml`, outside git | `platform/accounts.sh`: Gitea's admin API, Plane's Django shell | a password per tool, the first one random and shown once |

Roles:

| Role | Where defined | What reads it |
|---|---|---|
| `product_owner`, `engineer` of a project | `people` in `platform/projects/<name>/project.yaml`, in git | the orchestrator's policy (who may ask for what); Plane's setup Job (project members, `people.json`); `seed-projects.sh` (repository access, merge and approval allowlists) |
| platform operator | nowhere: the author works on the platform as the admins | — |

What hurts: a password per tool; two roles for every action; the platform operated through admin
accounts; Plane names users by id, so the orchestrator needs `people.json` from Plane's setup Job
to map usernames to ids; and a person's identity, their project roles and the tools' own
membership are kept in four places.

## The model to aim at

Plain RBAC with an identity provider:

- **One file of people**, `~/.config/ai-native-sdlc/accounts.yaml`, outside git because it holds
  e-mail addresses: users and their groups. Its fields are Keycloak's (`UserRepresentation`):
  `username`, `email`, `firstName`, `lastName`, `enabled`, and later `groups`, so the SSO step
  imports it as it is (keycloak-config-cli, or the admin API) and keeps Keycloak in step with it.
- **Groups are roles**, for example `example-webapp/product-owner`, `example-webapp/engineer`,
  `example-webapp/release-manager`, `platform/operator`, `platform/admin`.
- **Git keeps what a group may do**, never who is in it: Argo CD's RBAC
  (`g, platform/operator, role:operator`), Gitea's teams and branch protection by group, the
  orchestrator's policy by group.
- **The tools sign in through Keycloak:** Gitea (OIDC, with group-to-team sync), Argo CD (OIDC
  with the groups claim, directly or through Dex). Plane, through Gitea: see below.

### Plane: Keycloak through Gitea

Plane CE (v1.4.3, checked in its image) signs in with Google, GitHub, GitLab or Gitea
(`plane/authentication/provider/oauth/`); generic OIDC and SAML are paid (D9). Keycloak cannot
stand in for one of them: Plane calls the provider's own API, not OIDC endpoints. For Gitea it is
`<host>/login/oauth/access_token`, `<host>/api/v1/user` and `<host>/api/v1/user/emails`, taking
the primary *verified* address, so a shim in front of Keycloak would have to fake Gitea's API,
the fragile kind of workaround D9 rejects for Mattermost.

What works without one is a chain: Gitea signs in through Keycloak, and Plane through Gitea (an
OAuth2 application in Gitea; `GITEA_HOST`, `GITEA_CLIENT_ID`, `GITEA_CLIENT_SECRET` in Plane's
instance settings). One Keycloak session then covers Plane too: Plane sends the person to Gitea,
and Gitea, with no session of its own, to Keycloak. So D9's "Plane uses local accounts" can be
revised. To check: that the addresses Gitea takes from Keycloak count as verified, that Plane
links a sign-in to the user `accounts.sh` made by that address, and that Plane's server reaches
Gitea's public name (CA trust, question 5).
- **The orchestrator** takes a person's roles from their groups instead of the project files.
- **Admins stay local** in every tool, as break-glass accounts. **Services stay local** too: they
  hold tokens, not sessions.

## Actions and roles

Finer roles start from the actions there are, and who may take them today:

| Action | Today |
|---|---|
| assign a ticket to `@agent`, ask, answer its questions | any person of the project |
| approve a spec | `product_owner` |
| ask `@agent` to merge into `development` | `engineer` |
| approve a release pull request into `main` | `product_owner`, `engineer` |
| merge into `main` | `engineer` |
| prepare a release (delivery-loop group 10) | to be decided there |
| change the platform (push to `platform/ai-native-sdlc`) | `gitea_admin` |
| sync and operate in Argo CD | Argo CD's `admin` |
| administer Plane, Gitea, Argo CD | the admins |

Candidates named so far: product owner (manager), engineer (dev), release manager, platform
operator, platform admin (git admin). Each needs a row above that only it may take; a role that
adds no action is a label, not a role.

## Open questions for the SSO proposal

1. **Where project role membership lives:** Keycloak groups fed from `accounts.yaml` (one file,
   audited in Keycloak's admin events) or the project files in git (reviewed, with history, but a
   second list). The model above assumes the first.
2. **How the file reaches Keycloak:** keycloak-config-cli (declarative, removes what the file
   lost) or `accounts.sh` against Keycloak's admin API.
3. **Passwords:** Keycloak's required action `UPDATE_PASSWORD` at first sign-in replaces the
   random first password; resetting by e-mail needs SMTP, which the platform has none of.
4. **Plane through Gitea:** whether its Gitea OAuth links a sign-in to the user `accounts.sh`
   made (by e-mail address) and keeps the username the orchestrator and `people.json` rely on, or
   makes a new user with a generated one.
5. **CA trust in pods** (deferred to Keycloak in D13): the tools' back-channel calls to
   `keycloak.sdlc.internal` need the local CA.
6. **Roles in the orchestrator:** read from the token of a request it cannot see (requests arrive
   as Plane and Gitea webhooks, not sign-ins), so it needs group lookups in Keycloak by username.

## What SSO replaces

- `accounts.sh` creating users in each tool → Keycloak, with the tools' just-in-time accounts
  (and, for Plane, perhaps still a user made ahead for its username).
- A password per tool, `accounts.sh --reset` → Keycloak's.
- The two-role `people` lists and `seed-projects.sh`'s access by role → groups, mapped in git.
- Working on the platform as an admin → `platform/operator`.
