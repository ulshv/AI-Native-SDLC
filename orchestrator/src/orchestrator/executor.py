"""Agent runs as Kubernetes Jobs (design decision 7), and their results back into the queue.

A run's Job and its task ConfigMap are named <key>-<mode>-<n>, so starting a run twice finds the
first try's. Every run goes through pending_runs, oldest first, and starts while fewer than
max_active go; an advisory lock in Postgres keeps that true with two orchestrator pods during a
rollout. A finished Job's result (the pod's termination message) becomes an inbox event of the
thread that started it, unique by the Job's name and uid; on start the watcher reads every
finished Job again, so a run that ended while the orchestrator was down is not lost. Jobs
without a thread, such as those of platform/run-agent.sh, are not the orchestrator's.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .queue import Queue

log = logging.getLogger(__name__)

PART_OF = "agent-runs"
SELECTOR = f"app.kubernetes.io/part-of={PART_OF},sdlc/thread"
LOCK = 7_420_001  # pg_advisory_xact_lock key of the run limit


@dataclass(frozen=True)
class Run:
    name: str  # <key>-<mode>-<n>
    thread_id: str
    task: dict  # agent-runner's task.json


@dataclass
class Finished:
    name: str
    thread_id: str
    succeeded: bool
    result: dict = field(default_factory=dict)
    uid: str = ""


class Cluster(Protocol):
    def job_exists(self, name: str) -> bool: ...

    def active_jobs(self) -> int: ...

    def create_run(self, run: Run, manifest: dict, task_json: str) -> None:
        """Creates the run's ConfigMap and Job, each unless it exists: a retry completes a half-done start."""

    def finished_jobs(self) -> list[Finished]: ...

    def watch_finished(self, stop: threading.Event): ...  # yields Finished


def run_name(key: str, mode: str, n: int) -> str:
    return f"{key.lower()}-{mode}-{n}"


def job_manifest(run: Run, image: str, namespace: str) -> dict:
    """The run's Job: 30 minutes, no retry, kept an hour; not root, no capabilities, a read-only root
    file system, no Kubernetes token (the same shape as platform/run-agent.sh)."""
    labels = {"app.kubernetes.io/part-of": PART_OF, "sdlc/thread": run.thread_id,
              "sdlc/key": run.task["ticket"]["key"].lower(), "sdlc/mode": run.task["mode"]}
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": run.name, "namespace": namespace, "labels": labels},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 1800, "ttlSecondsAfterFinished": 3600,
                 "template": {"metadata": {"labels": labels}, "spec": {
                     "serviceAccountName": "agent-run", "automountServiceAccountToken": False, "restartPolicy": "Never",
                     "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000,
                                         "seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [{
                         "name": "agent", "image": image,
                         "env": [{"name": "OPENCODE_CONFIG", "value": "/config/opencode.json"},
                                 {"name": "HOME", "value": "/home/agent"},
                                 {"name": "LLM_API_KEY", "valueFrom": {"secretKeyRef": {"name": "llm-api-key", "key": "LLM_API_KEY"}}}],
                         "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                             "capabilities": {"drop": ["ALL"]}},
                         "resources": {"requests": {"cpu": "200m", "memory": "512Mi"}, "limits": {"memory": "2Gi"}},
                         "volumeMounts": [{"name": "task", "mountPath": "/task"}, {"name": "config", "mountPath": "/config"},
                                          {"name": "gitea", "mountPath": "/secrets/gitea"}, {"name": "work", "mountPath": "/work"},
                                          {"name": "home", "mountPath": "/home/agent"}, {"name": "tmp", "mountPath": "/tmp"}]}],
                     "volumes": [{"name": "task", "configMap": {"name": run.name}},
                                 {"name": "config", "configMap": {"name": "opencode-config"}},
                                 {"name": "gitea", "secret": {"secretName": "gitea-agent-runner",
                                                              "items": [{"key": "token", "path": "token"}]}},
                                 {"name": "work", "emptyDir": {}}, {"name": "home", "emptyDir": {}},
                                 {"name": "tmp", "emptyDir": {}}]}}}}


class Executor:
    def __init__(self, cluster: Cluster, pool: ConnectionPool, queue: Queue, image: str, namespace: str,
                 max_active: int = 2):
        if not image:
            raise ValueError("no image for agent runs")
        self.cluster, self.pool, self.queue = cluster, pool, queue
        self.image, self.namespace, self.max_active = image, namespace, max_active

    def start(self, run: Run) -> str:
        """Queues the run and starts the waiting ones while there is room. Idempotent by name."""
        if self.cluster.job_exists(run.name):
            self._create(run)  # completes a start that stopped between the ConfigMap and the Job
            return "started before"
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO pending_runs (name, thread_id, task) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (run.name, run.thread_id, Jsonb(run.task)))
        self.start_waiting()
        if self.cluster.job_exists(run.name):
            return "started"
        log.info("run %s waits: %d runs are going", run.name, self.max_active)
        return "waiting"

    def _create(self, run: Run) -> None:
        self.cluster.create_run(run, job_manifest(run, self.image, self.namespace), json.dumps(run.task))
        log.info("started run %s for thread %s", run.name, run.thread_id)

    def start_waiting(self) -> None:
        """Starts waiting runs, oldest first, while fewer than max_active go."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            while self.cluster.active_jobs() < self.max_active:
                row = conn.execute("SELECT name, thread_id, task FROM pending_runs ORDER BY created_at, name LIMIT 1").fetchone()
                if row is None:
                    return
                run = Run(*row)
                self._create(run)
                conn.execute("DELETE FROM pending_runs WHERE name = %s", (run.name,))

    def record(self, finished: Finished) -> None:
        """A finished run becomes an event of its thread, once per Job (its name and uid)."""
        if not finished.thread_id:
            return
        self.queue.put("job", f"{finished.name}/{finished.uid}", finished.thread_id,
                       {"job": finished.name, "thread_id": finished.thread_id, "succeeded": finished.succeeded,
                        "result": finished.result})

    def resync(self) -> None:
        for finished in self.cluster.finished_jobs():
            self.record(finished)
        self.start_waiting()

    def watch(self, stop: threading.Event) -> None:
        """Runs in its own thread: records finished runs and fills the room they leave."""
        while not stop.is_set():
            try:
                self.resync()
                for finished in self.cluster.watch_finished(stop):
                    self.record(finished)
                    self.start_waiting()
            except Exception:  # noqa: BLE001 - the API server went away; read everything again
                log.exception("watching agent runs failed")
                stop.wait(5)


class KubernetesCluster:
    """The Cluster port on the Kubernetes API, in the agent runs' namespace."""

    def __init__(self, namespace: str):
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.batch, self.core, self.namespace = client.BatchV1Api(), client.CoreV1Api(), namespace
        self.selector = SELECTOR

    def job_exists(self, name: str) -> bool:
        from kubernetes.client.exceptions import ApiException

        try:
            self.batch.read_namespaced_job(name, self.namespace)
            return True
        except ApiException as error:
            if error.status == 404:
                return False
            raise

    def active_jobs(self) -> int:
        jobs = self.batch.list_namespaced_job(self.namespace, label_selector=self.selector).items
        return sum(1 for job in jobs if not finished(job))

    def create_run(self, run: Run, manifest: dict, task_json: str) -> None:
        from kubernetes.client.exceptions import ApiException

        def unless_exists(create):
            try:
                return create()
            except ApiException as error:
                if error.status != 409:
                    raise

        # The ConfigMap first, so the pod never waits for its task; then the Job; then the
        # ConfigMap is owned by the Job, so it goes when the Job does.
        unless_exists(lambda: self.core.create_namespaced_config_map(self.namespace, {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": run.name, "labels": manifest["metadata"]["labels"]},
            "data": {"task.json": task_json}}))
        unless_exists(lambda: self.batch.create_namespaced_job(self.namespace, manifest))
        job = self.batch.read_namespaced_job(run.name, self.namespace)
        self.core.patch_namespaced_config_map(run.name, self.namespace, {"metadata": {"ownerReferences": [
            {"apiVersion": "batch/v1", "kind": "Job", "name": run.name, "uid": job.metadata.uid}]}})

    def _finished(self, job) -> Finished:
        pods = self.core.list_namespaced_pod(self.namespace, label_selector=f"job-name={job.metadata.name}").items
        message = ""
        for pod in pods:
            for status in pod.status.container_statuses or []:
                terminated = status.state.terminated
                if terminated and terminated.message:
                    message = terminated.message
        try:
            result = json.loads(message) if message else {}
        except json.JSONDecodeError:
            result = {}
        if not result:
            reason = next((c.message or c.reason for c in job.status.conditions or [] if c.type == "Failed"), "")
            result = {"result": "failed", "reason": f"the run ended without a result: {reason or 'no message'}"}
        return Finished(job.metadata.name, job.metadata.labels.get("sdlc/thread", ""),
                        bool(job.status.succeeded), result, job.metadata.uid)

    def finished_jobs(self) -> list[Finished]:
        jobs = self.batch.list_namespaced_job(self.namespace, label_selector=self.selector).items
        return [self._finished(job) for job in jobs if finished(job)]

    def watch_finished(self, stop: threading.Event):
        from kubernetes import watch

        w = watch.Watch()
        for item in w.stream(self.batch.list_namespaced_job, self.namespace, label_selector=self.selector,
                             timeout_seconds=300):
            if stop.is_set():
                w.stop()
                return
            job = item["object"]
            if item["type"] in ("ADDED", "MODIFIED") and finished(job):
                yield self._finished(job)


def finished(job) -> bool:
    return any(c.type in ("Complete", "Failed") and c.status == "True" for c in job.status.conditions or [])
