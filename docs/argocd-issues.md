# Argo CD: known issues

How Argo CD misbehaves on this platform, how to see it and get out of it, and what a lasting
fix would take. One section per issue; a fixed one says by which commit.

[← back to README](../README.md)

---

## A commit whose image never builds holds back the next ones

**Seen** 2026-11-13: `009b384` failed CI's tests, `ede685d` fixed them a few minutes later, and
the orchestrator still ran `1d2912f` half an hour on.

**Symptoms.** CI is green for the latest commit and its images are in the registry, yet the
orchestrator's pod keeps an older image tag. `orchestrator` shows OutOfSync with an operation
"Running", whose message reads "one or more synchronization tasks completed unsuccessfully.
Retrying attempt #N". The Job `sdlc/orchestrator-migrate` runs, or failed with
`DeadlineExceeded`, on the image of the failed commit.

```bash
kubectl get application -n argocd orchestrator -o json |
  jq '.status.operationState | {phase, retryCount, revisions: .operation.sync.revisions, message}'
kubectl get job -n sdlc orchestrator-migrate -o jsonpath='{.spec.template.spec.containers[0].image}'
```

**Why.** The orchestrator's image tag is the commit Argo CD syncs (`image.tag:
$ARGOCD_APP_REVISION`, D19), so Argo CD can start a sync before CI has pushed the image, or for
a commit whose image never comes. The PreSync Job `orchestrator migrate` then waits for its
image, up to `activeDeadlineSeconds` (15 minutes, `apps/orchestrator/chart/templates/migrate.yaml`),
and a failed sync is retried up to 5 times with a backoff from 1 to 10 minutes
(`apps/orchestrator/app.yaml`). That retry is there on purpose, for the image that is merely
late. But an operation keeps the revision it started with: its retries never move to a newer
commit, and automated sync starts no new operation while one runs. A red commit therefore holds
back every later one for up to six attempts of 15 minutes plus the backoff, about two hours.
The running pod is unharmed meanwhile: the Deployment is not touched before the PreSync Job
succeeds.

**Getting out.** End the operation, as `argocd app terminate-op orchestrator` does; the next
automated sync takes `main`'s head:

```bash
kubectl patch application orchestrator -n argocd --type merge \
  -p '{"status":{"operationState":{"phase":"Terminating"}}}'
kubectl get application -n argocd orchestrator -w   # Running at the new commit, then Synced
```

Only do it once the commit after the red one is green; otherwise the new operation waits again.

**Lasting fixes, not made yet:**

- **CI moves the tag** (recommended): once the images are pushed, CI commits the tag to git, say
  `image.tag` in the chart's values, and Argo CD syncs only what CI built. A red commit is never
  referenced, and the PreSync Job no longer waits for an image. It costs a bot commit per build
  on `main`, which CI must not build again, and a token that lets the platform's CI push to this
  repository. Argo CD Image Updater with write-back to git is the same, as a component.
- **CI starts the sync:** the Application without automated sync, and CI's last step asks Argo CD
  to sync the commit it built. No bot commits, but CI holds an Argo CD token that can sync the
  platform, and a change in git alone no longer reaches the cluster.
- **Shorter waits only** (a smaller `activeDeadlineSeconds` or `retry.limit`): shrinks the two
  hours, fixes nothing, and makes a merely late image fail sooner.

The same holds for every Application that takes its image tag from `$ARGOCD_APP_REVISION` with
a hook waiting for it; the orchestrator is the only one today.

---

## An environment runs ahead of its image

**Seen** 2026-11-14 on the preview `ewa-7` (task 9.3), with the product's CI switched off on
purpose: the Application stayed Synced and Degraded, its pod in `ImagePullBackOff`, for as long
as the pull request was open.

**Why.** dev, prod and previews take the image tag from the commit they deploy (D19), as the
orchestrator does, and nothing waits for CI. Unlike the orchestrator's, the product's chart has
no PreSync hook, so no sync is held back: the new ReplicaSet waits for its image while the old
one keeps serving, and the pod starts as soon as CI pushes the image. A commit whose image never
builds leaves the environment Degraded on the new ReplicaSet until a later commit replaces it.
In Deploying to QA the orchestrator gives up waiting after 15 minutes and says why, with CI's
run of the commit (`orchestrator/README.md`, Previews).

**Lasting fix:** the same as above, CI moving the tag: an environment then only ever points at
an image that exists.
