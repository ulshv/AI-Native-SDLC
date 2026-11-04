# Tasks

> **TL;DR**
> - **12 groups, 63 tasks:** spikes → platform additions → CI → Plane setup → `example-webapp` → agent runs → orchestrator → the loop end to end → environments → releases → hardening → docs and final checks.
> - **CI comes early,** because it builds the orchestrator and the agent image: from group 3 on, a push to `main` builds the platform's images and Argo CD rolls them out.
> - **The loop works after group 8:** a ticket goes from assignment to a merge into `development` with both gates; previews and releases come after it, so a demo can happen there.
> - **Spikes first (group 1):** OpenCode unattended with an OpenAI-compatible provider, the wildcard resolver against the VPN client, Plane's setup by script and its webhook payloads, Gitea's merge allowlists, the ApplicationSet matrix, the runner in host mode with BuildKit. Any of them can send us back to the design.
> - **Verified at the end:** a full rebuild with both clones builds its own images, passes every smoke check and runs a ticket to On dev; a release reaches production and marks its tickets Released.
> - **Manual steps for you:**
>   - `LLM_API_KEY` in `secrets.env` (your RouterAI key);
>   - the sudo password for bootstrap runs (k3s `registries.yaml`, the wildcard resolver) and for the resolver spike;
>   - reviewing and committing the seed in `~/Projects/sdlc/example-webapp`, a second repository;
>   - any Plane step the setup script cannot do (spike 1.3);
>   - turning the VPN client on and off and going offline in the resolver checks;
>   - acting as product owner and engineer in the end-to-end runs: comments, approvals, merging the release;
>   - pushing `main` to the GitHub mirror.

## 1. Spikes

- [ ] 1.1 Run `opencode run` headless in a container with one OpenAI-compatible provider block (RouterAI, DeepSeek V4 Flash), on a scratch repository with OpenSpec initialised for OpenCode, for `/opsx:propose` and `/opsx:apply`. Verify both finish without a prompt, record duration and token cost, and confirm the questions-file convention from design decision 7 works. Report back before building further if the model cannot finish either command
- [ ] 1.2 Prepare a reversible script for the operator that starts `dnsmasq` on `127.0.0.2:53` only, answering `*.sdlc.internal` with `127.0.0.1`, and routes `~sdlc.internal` to it with a systemd-resolved drop-in. Verify, with the VPN client on and then offline, that `a.b.c.sdlc.internal` resolves to `127.0.0.1` on the host, then undo it. Report back if the VPN client intercepts it (fallback in design decision 13)
- [ ] 1.3 In a throwaway run of Plane's API image, check with a Django-shell script what design decision 14 needs: create a user and its API token, a project with custom states, members, and a webhook. Capture real webhook payloads for an assignee change and for a comment that mentions a user, and save them as test fixtures. Report back on anything that needs a manual step
- [ ] 1.4 On Gitea, check branch protection with a merge allowlist (a test user allowed on one branch and refused on another, through the API), and that a branch with a required approval refuses a merge without one. Capture the `pull_request_review` and `issue_comment` webhook payloads as fixtures. Undo the test changes afterwards
- [ ] 1.5 Apply a throwaway ApplicationSet with a matrix of the git files generator and Gitea's `pullRequest` generator, with `goTemplate` and `regexFind` on a `feat/TEST-1_x` branch, against a scratch repository. Verify an Application named after `test-1` appears for a labelled PR and disappears when the PR closes. Report back if the matrix does not work (fallback in design decision 11)
- [ ] 1.6 Start a throwaway runner pod as in design decision 12 (public Ubuntu runner image, `act_runner` from an init container, host mode, rootless BuildKit sidecar with the local CA), registered on a scratch repository. Verify a workflow builds and pushes an image to `gitea.sdlc.internal`, and that k3s pulls it with a hand-written `registries.yaml`. Undo the test changes afterwards
- [ ] 1.7 Record each spike's outcome in `design.md` (the decision it confirms or changes), and verify the TL;DR still matches the body

## 2. Platform additions: secrets, registry, accounts, database

- [ ] 2.1 Add `LLM_API_KEY` (external, required), `PG_ORCHESTRATOR_PASSWORD`, `PLANE_WEBHOOK_SECRET` and `GITEA_WEBHOOK_SECRET` to `platform/secrets.manifest` and regenerate `secrets.env.example`. Verify preflight fails naming `LLM_API_KEY` when it is missing, and that a second run leaves `secrets.env` unchanged
- [ ] 2.2 Enable Gitea's container registry, create the read-only `registry-pull` token, and write `/etc/rancher/k3s/registries.yaml` (CA and token) in the host phase, restarting k3s only when the file changed. Verify a pod in a fresh namespace pulls an image from `gitea.sdlc.internal/sdlc/` without a pull secret, and that a re-run does not restart k3s; add the check to `platform/smoke.sh`
- [ ] 2.3 Create the Gitea accounts `agent` and `agent-runner` and their tokens in the git hosting phase, stored only in cluster Secrets. Verify both accounts exist, a re-run creates nothing new, and no token appears in the working tree (existing smoke check)
- [ ] 2.4 Add an Argo CD credential template for `http://gitea-http.gitea.svc:3000/sdlc/` in the gitops phase. Verify Argo CD can read a second repository in the `sdlc` organization without a per-repository secret
- [ ] 2.5 Add the `orchestrator` database and role to `platform/apps/databases/`. Verify the orchestrator's credentials open only their own database, extending the existing isolation smoke check
- [ ] 2.6 Document the registry, the agent accounts and the new secrets in `platform/README.md`, including how to undo `registries.yaml`, and verify every command it lists runs as written

## 3. CI

- [ ] 3.1 Add `platform/apps/ci/` from design decision 12: the runners `ci-platform` (this repository, label `platform`) and `ci-products` (the `sdlc` organization, label `ubuntu-latest`), each with a BuildKit sidecar, registration tokens from the bootstrap, and a NetworkPolicy. Verify both runners show as online in Gitea, and that no pod in `ci` is privileged or mounts a container runtime socket
- [ ] 3.2 Add `.gitea/workflows/images.yaml` to this repository: on every push to `main`, build every image directory it lists that exists (`orchestrator/`, `agent-runner/`) and push it as `:<commit>`. Verify on a temporary `wip/ci` branch with a test image directory that the run happens on `ci-platform` and the image appears in the registry, then delete the branch
- [ ] 3.3 Verify that a workflow of a scratch repository in the `sdlc` organization runs on `ci-products` and never on `ci-platform`, then remove the scratch repository
- [ ] 3.4 Document both runners, the `images` workflow, image tags and how a rebuild gets its first images in `platform/README.md`, and verify against 3.2

## 4. Plane setup for the loop

- [ ] 4.1 Implement the Plane setup hook Job from design decision 14 (agent user and API key Secret, workspace, project `EWA` with the loop's states, members from `platform/projects/`, webhook to the orchestrator with `PLANE_WEBHOOK_SECRET`), as far as spike 1.3 allows. Verify a second sync changes nothing and `agent` can be assigned and mentioned in `EWA`
- [ ] 4.2 Add smoke checks that project `EWA` has every loop state and that the webhook exists. Document any manual step from spike 1.3 in `platform/README.md`, and verify the smoke check fails when that step is skipped

## 5. `example-webapp` and connecting it

- [ ] 5.1 Create the app in `~/Projects/sdlc/example-webapp` (Vite, React, TypeScript): login accepting `demo` / `demo`, an orders dashboard from a local JSON file, Vitest tests. Verify `npm ci && npm test && npm run build` pass on a fresh clone
- [ ] 5.2 Add a static nginx `Dockerfile` and a Helm chart in `deploy/chart` (Deployment, Service, Ingress with `cert-manager.io/cluster-issuer: local-ca`, values for host and image tag); the image serves `/version.json` with the commit it was built from. Verify `helm template` renders with a host and a tag, and that a locally built image reports its commit
- [ ] 5.3 Initialise OpenSpec for OpenCode, write specs `auth` and `dashboard` for what the app does, and add the agent instructions for unattended runs (questions file, no chat questions). Verify `openspec validate --all --strict` passes and the `/opsx` commands exist for OpenCode
- [ ] 5.4 Add `.gitea/workflows/ci.yaml` and `release.yaml` from design decision 12 with `runs-on: ubuntu-latest`, and verify they parse; `ci.yaml` runs in 5.8, `release.yaml` in group 10
- [ ] 5.5 Create `main` without a version tag and `development` from it in the local clone, and verify `git log` shows both branches at the same commit and `git tag` is empty. The operator commits this repository's seed after reviewing it
- [ ] 5.6 Add `platform/projects/example-webapp/project.yaml` (Plane project, repository, base branch, clone path, domain, `previews: false` until group 9, merge allowlist, people with usernames and roles), and verify it parses with the schema the orchestrator will use
- [ ] 5.7 Create `platform/seed-projects.sh` from design decision 9: it reads `platform/projects/*/project.yaml`, stops naming a missing clone before changing anything, and creates a missing repository from its clone, with all branches and tags, write access for `agent` and `agent-runner`, and branch protection from design decision 8. Verify with a smoke check that `agent` is refused a merge into `main` and allowed into `development`, and that a re-run leaves an existing repository unchanged
- [ ] 5.8 Push a commit to a feature branch of `example-webapp`. Verify the tests run on `ci-products`, the image `:<sha>` exists, and the PR shows the check; then push a failing test and verify no image is stored and the check fails
- [ ] 5.9 Document target projects, the project directory, the clone location and adding a project in `platform/README.md`, and verify the steps match what `seed-projects.sh` does

## 6. Agent runs

- [ ] 6.1 Write the `agent-runner` image (Node LTS, pinned OpenCode and OpenSpec CLI, git) with the entrypoint for modes `propose`, `apply`, `fix` and `archive` and the termination-message result from design decision 7. Verify the `images` workflow builds it on the push to `main` and the registry holds it tagged with that commit
- [ ] 6.2 Add `platform/apps/agents/`: namespace `sdlc-agents`, ServiceAccount, NetworkPolicy and the OpenCode config with one OpenAI-compatible provider block. Verify from a test pod there that Gitea and the model provider are reachable and that Postgres, Plane and the orchestrator are not; add it to `platform/smoke.sh`
- [ ] 6.3 Run a `propose` Job by hand for a sample task on `example-webapp`. Verify it pushes a `feat/EWA-0_<slug>` branch with only an OpenSpec change and ends with `spec_ready`, and that a deliberately vague task ends with `questions` and pushes nothing
- [ ] 6.4 Run an `apply` Job by hand on that branch. Verify the branch gains the code with the change still unarchived, the project's tests pass, and the result is `code_ready`. Then run an `archive` Job on it and verify one commit archives the change into the specs and the result is `archived`; delete the sample branch afterwards
- [ ] 6.5 From a Job, try to merge a PR and to push to `development` with the `agent-runner` token, and verify Gitea refuses both
- [ ] 6.6 Document how to run a Job by hand and read its result in `platform/README.md`, and verify the commands work as written

## 7. Orchestrator

- [ ] 7.1 Create the `orchestrator/` Python project with pinned dependencies (FastAPI, LangGraph, its Postgres checkpointer, psycopg, the Kubernetes client, httpx, LangChain's OpenAI and DeepSeek chat clients), a `Dockerfile` and a test runner. Verify the tests run and the service starts against a local Postgres
- [ ] 7.2 Implement `thread_link` and routing from design decision 4, the queue interface with its Postgres implementation, and the worker. Verify with tests that a duplicate delivery takes effect once, that events for one thread apply in arrival order, that a ticket comment and a PR review of one item reach the same thread, that an object cannot be linked to two threads, and that rows left unprocessed by a crash are handled after restart
- [ ] 7.3 Implement the `Tracker` and `CodeHost` ports and their Plane and Gitea adapters, and the echo filter, from design decision 6, using the payload fixtures from spikes 1.3 and 1.4. Verify with tests that each fixture becomes the expected event, that comments without `@agent` produce nothing, and that the agent's own events are dropped
- [ ] 7.4 Implement loading of `platform/projects/*/project.yaml` and resolving people and roles. Verify with tests that a known person gets their roles in both tools and an unknown account is refused
- [ ] 7.5 Implement the model client chosen by the model's prefix, intent reading and the policy table from design decision 5. Verify with tests that `deepseek/*` gets `ChatDeepSeek` and other models `ChatOpenAI`, every row of the table, the refusals for a wrong role and a wrong phase, and the clarifying reply below the confidence threshold; verify one live call reads "@agent looks good, go ahead" as `approve`
- [ ] 7.6 Implement the Plane and Gitea clients (status, comments with event markers, PR find-or-create, label, merge, webhook registration). Verify with tests against fakes that retrying an action does not repeat it
- [ ] 7.7 Implement the executor from design decision 7: Job and ConfigMap creation with deterministic names, at most two active runs, the agent image from the orchestrator's own commit, and the Job watcher with resync on start. Verify with tests on a fake cluster that a third run waits and that a result finished during downtime is picked up
- [ ] 7.8 Implement the graph for intake, Drafting, Needs info, Spec review, Implementing, Deploying to QA (skipped while `previews` is off), Code review, On dev and Closed per `work-item-orchestration`, including the "Implementing a fix" and "Fix is ready" replies where the request came from, the `archive` run after the code is approved (merging right after it when the approval was a merge request), and, when a person merged first, the "not archived" comment and the archive PR on "@agent ok, create archive PR". Verify each transition and each refusal with tests
- [ ] 7.9 Add the thread API from design decision 4 (`/threads/{thread_id}`, its messages, and lookup by link). Verify with tests that a thread shows its phase and links and is found by its PR
- [ ] 7.10 Add `platform/apps/orchestrator/` (Deployment with a rolling update that keeps the old pod until the new one is ready, Service, config, project files through `configMapGenerator`, RBAC from design decision 7, Secrets, image tag from `$ARGOCD_APP_REVISION`). Verify a push to `main` rolls out the new image within 10 minutes, a commit whose build fails leaves the old pod running, the Gitea webhooks are registered, and a smoke check covers health and webhooks
- [ ] 7.11 Write `orchestrator/README.md` (layout, local run, tests, configuration, how a change reaches the cluster), and verify the commands work as written

## 8. The loop end to end

- [ ] 8.1 Run the loop on a real ticket in `EWA`: assign to `@agent`, answer a question, approve with a comment, get the code in the same PR, ask "@agent ok, merge it into dev" and see the archive commit land before the merge. Verify every status and comment in Plane and the PR state in Gitea match the specs, and record the lead time
- [ ] 8.2 Restart the orchestrator while an item waits in Spec review, and redeliver a webhook from Gitea's UI. Verify the item continues as before and the redelivery changes nothing
- [ ] 8.3 Ask to merge as a person without the engineer role, and ask to merge an item in Spec review. Verify both get a refusal and nothing changes
- [ ] 8.4 Update `platform/README.md` with how to watch an item and what to do when a run fails, and verify against the run from 8.1

## 9. Environments

- [ ] 9.1 Add the wildcard resolver to the bootstrap's host phase per spike 1.2 (or its fallback). Verify, with the VPN client on and offline, that a new name at any depth under `sdlc.internal` resolves on the host without editing `hosts.txt`, and document how to undo it
- [ ] 9.2 Add the dev and prod ApplicationSet from design decision 11. Verify `https://dev.example-webapp.sdlc.internal` and `https://example-webapp.sdlc.internal` load without certificate warnings and run the image of their branch's head commit
- [ ] 9.3 Add the preview ApplicationSet, turn `previews` on for `example-webapp`, and make the orchestrator label the PR on `code_ready` and poll the preview's `/version.json`. Verify the item goes Implementing → Deploying to QA → Code review with the preview link once the preview serves the commit, that the preview follows a pushed fix and disappears within 5 minutes of the merge, and that a failed build moves the item to Code review after 15 minutes with the reason
- [ ] 9.4 Add smoke checks for the environments and document them in `platform/README.md`, and verify the checks fail when the dev Application is out of sync

## 10. Releases

- [ ] 10.1 Implement `prepare_release` and the handling of Gitea's `release` event from design decision 15. Verify with tests the version rule (minor bump, `v0.1.0` first), the ticket list, the "nothing to release" reply, and the refusal to merge a release
- [ ] 10.2 Run a release: "@agent let's prepare a release" in Plane, approve and merge it in Gitea as a person. Verify tag `v0.1.0` and its Gitea release, the image tagged `0.1.0`, production running it, and every listed ticket Released with a comment
- [ ] 10.3 Verify that the `agent` account cannot merge the release PR even through the API, and document the release flow in `platform/README.md`

## 11. Hardening

- [ ] 11.1 Verify webhook signatures (`X-Plane-Signature`, `X-Gitea-Signature`) with the generated secrets. Verify with tests that a wrong or missing signature gets 401 and records nothing
- [ ] 11.2 Comment in the ticket when a run fails or times out. Verify with tests for a failed run and for a run past its deadline
- [ ] 11.3 Extend the smoke checks for NetworkPolicies: `sdlc-agents` and `ci` cannot reach Postgres, Plane or the orchestrator. Verify they pass and fail when a policy is removed

## 12. Docs and final checks

- [ ] 12.1 Update `README.md` and `README_ru.md`: the example keeps its Mattermost step (it arrives with roadmap step 4), the archive commit comes after the code approval, the Deploying to QA status comes between Implementing and Code review, the first release is `v0.1.0`, the harness works with any OpenAI-compatible provider, and the architecture describes the orchestrator's threads and their links instead of a record per work item (diagrams through the `diagrams` skill); status note, roadmap step 1 and the "Run it" section reflect what runs. Verify the diagrams render in light and dark mode
- [ ] 12.2 Update `docs/decisions.md`: D18 (previews start after the apply run, CI gating later), D20 (the change is archived by its own commit after the code approval), D23 (any OpenAI-compatible provider, the model client by prefix, LiteLLM Proxy as the candidate for the Langfuse step) and D13 if the resolver replaced part of it; new entries for the registry, threads first with `thread_link`, platform images built by CI on their own runner, the queue in Postgres behind an interface (NATS when several services consume events), the project directory, CI in host mode and the Plane setup Job. Mark D17–D23 accepted where built
- [ ] 12.3 Measure memory with a Job and a CI build running, and replace the estimates in the RAM table of both READMEs
- [ ] 12.4 Rebuild the cluster from scratch with both clones present: run `bootstrap.sh`, wait for the first `images` run and the orchestrator, then `seed-projects.sh` and `platform/smoke.sh`, and run one short ticket through the loop. Verify every check passes and the ticket reaches On dev

## Workflow follow-up

- Archive the change with `/opsx:archive` after group 12 passes and the operator has reviewed it.
- Push `main` to Gitea and, by hand, to the GitHub mirror.
