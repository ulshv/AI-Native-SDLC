"""Metrics for Prometheus at /metrics: what an operator watches. Failures are counted where they
end something: an event's attempt, a round of the run watcher. The queue's state is read when
scraped, from the unfinished events only (the inbox_pending index); the executor keeps the number
of runs going, which it counts anyway."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from prometheus_client import CollectorRegistry, Counter, Gauge
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

EXTERNAL_ERRORS = Counter(
    "orchestrator_external_errors",
    "Event attempts and watch rounds that failed on a call to the outside, by whom it went to",
    ["target"],
    registry=None,
)
for target in ("plane", "gitea", "model", "kubernetes"):  # every series from zero
    EXTERNAL_ERRORS.labels(target)
GIVEN_UP = Counter("orchestrator_events_given_up", "Events closed with an error after their attempts", registry=None)
RUNS_ACTIVE = Gauge("orchestrator_runs_active", "Agent runs going, as last counted", registry=None)

# Whom an error that is not an ApiError (which names its target) came from, by its package.
FROM_PACKAGE = {"openai": "model", "kubernetes": "kubernetes", "urllib3": "kubernetes"}


def failed_call(error: BaseException) -> None:
    """Counts an error that came from a call to the outside; any other is the code's own."""
    target = getattr(error, "target", "") or FROM_PACKAGE.get(type(error).__module__.split(".")[0])
    if target:
        EXTERNAL_ERRORS.labels(target).inc()


STATE = """
SELECT count(*), coalesce(extract(epoch FROM clock_timestamp() - min(received_at)), 0),
       (SELECT count(*) FROM pending_runs)
FROM inbox WHERE processed_at IS NULL
"""


class State(Collector):
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            with self.pool.connection(timeout=2) as conn:
                row = conn.execute(STATE).fetchone()
        except Exception:
            log.warning("reading the queue's state for the metrics failed", exc_info=True)
            return
        if row:
            depth, oldest, waiting = row
            yield GaugeMetricFamily("orchestrator_queue_depth", "Events recorded and not done yet", value=depth)
            yield GaugeMetricFamily(
                "orchestrator_queue_oldest_seconds", "Age of the oldest event not done", value=oldest
            )
            yield GaugeMetricFamily("orchestrator_runs_waiting", "Agent runs waiting for room", value=waiting)


def registry(pool: ConnectionPool) -> CollectorRegistry:
    metrics = CollectorRegistry()
    for metric in (EXTERNAL_ERRORS, GIVEN_UP, RUNS_ACTIVE, State(pool)):
        metrics.register(metric)
    return metrics
