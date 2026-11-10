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
from .queue import PostgresQueue
from .worker import Workers

log = logging.getLogger("orchestrator")


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
    import json

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from . import intents
    from .adapters.gitea import GiteaCodeHost
    from .adapters.plane import PlaneTracker
    from .graph import Dispatcher, Services, build
    from .projects import LiveProjects

    queue = PostgresQueue(pool, settings.max_attempts)
    links = Links(pool)
    stop = threading.Event()
    executor = None
    if settings.agent_image:
        from .executor import Executor, KubernetesCluster

        executor = Executor(KubernetesCluster(settings.agent_namespace), pool, queue, settings.agent_image,
                            settings.agent_namespace, settings.max_active_runs)
        threading.Thread(target=executor.watch, args=(stop,), name="runs-watcher", daemon=True).start()
    else:
        log.warning("no agent image (ORCHESTRATOR_COMMIT or agent_image): agent runs are off")
    projects = LiveProjects(settings.projects_dir)
    log.info("projects: %s", ", ".join(p.name for p in projects.all) or "none")
    svc = Services(
        projects=projects, links=links, queue=queue,
        tracker=PlaneTracker(settings.plane_url, settings.plane_api_key, json.loads(settings.plane_people)),
        code_host=GiteaCodeHost(settings.gitea_url, settings.gitea_token), executor=executor,
        chat=intents.chat_model(settings.intent_model, settings.llm_base_url, settings.llm_api_key),
        agent_model=settings.agent_model, threshold=settings.intent_threshold)
    saver_pool = ConnectionPool(settings.database_url, min_size=1, max_size=settings.workers + 2,
                                kwargs={"autocommit": True, "row_factory": dict_row}, open=True)
    dispatcher = Dispatcher(svc, build(svc, PostgresSaver(saver_pool)))
    workers = Workers(queue, dispatcher, settings.workers)
    workers.start()
    try:
        uvicorn.run(create_app(pool, queue, links, dispatcher.item), host=host, port=port, log_config=None)
    finally:
        stop.set()
        workers.stop()
        saver_pool.close()
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
    if not settings.llm_api_key:
        log.error("LLM_API_KEY is not set")
        return 1
    return serve(settings, args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
