"""The orchestrator's entry point.

  orchestrator migrate   create or update the tables (a Job before every rollout)
  orchestrator serve     the webhooks, the thread API, the workers and the agent runs' watcher
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from . import config, db
from .links import Links
from .queue import Delivery, PostgresQueue
from .worker import Workers

log = logging.getLogger("orchestrator")


def handle(delivery: Delivery) -> None:
    """Placeholder until the graph (task 7.8): the event is only logged."""
    log.info("event %s from %s for %s", delivery.delivery_id, delivery.source, delivery.routing_key)


def migrate(settings: config.Settings) -> int:
    pool = db.connect(settings.database_url, size=2)
    db.migrate(pool, settings.database_url)
    log.info("the schema is up to date")
    return 0


def serve(settings: config.Settings, host: str, port: int) -> int:
    import uvicorn

    from .api import create_app

    pool = db.connect(settings.database_url, size=settings.workers + 6)
    missing = db.missing_tables(pool)
    if missing:
        log.error("tables %s are missing: run `orchestrator migrate` first", ", ".join(missing))
        return 1
    queue = PostgresQueue(pool, settings.max_attempts)
    workers = Workers(queue, handle, settings.workers)
    workers.start()
    stop = threading.Event()
    if settings.agent_image:
        from .executor import Executor, KubernetesCluster

        executor = Executor(KubernetesCluster(settings.agent_namespace), pool, queue, settings.agent_image,
                            settings.agent_namespace, settings.max_active_runs)
        threading.Thread(target=executor.watch, args=(stop,), name="runs-watcher", daemon=True).start()
    else:
        log.warning("no agent image (ORCHESTRATOR_COMMIT or agent_image): agent runs are off")
    try:
        uvicorn.run(create_app(pool, queue, Links(pool)), host=host, port=port, log_config=None)
    finally:
        stop.set()
        workers.stop()
        pool.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="create or update the tables")
    run = commands.add_parser("serve", help="run the service")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = config.load()
    if not settings.database_url:
        log.error("DATABASE_URL is not set")
        return 1
    if args.command == "migrate":
        return migrate(settings)
    return serve(settings, args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
