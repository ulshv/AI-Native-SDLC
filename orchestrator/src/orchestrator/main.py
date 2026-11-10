"""The orchestrator's entry point: settings, connections and clients for the agent (agent.py).

  orchestrator migrate   create or update the tables (a Job before every rollout)
  orchestrator serve     the webhooks, the thread API, the workers and the agent runs' watcher
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from contextlib import ExitStack

import uvicorn
from psycopg_pool import ConnectionPool

from . import agent, config, db, intents
from .adapters.gitea import GiteaCodeHost
from .adapters.plane import PlaneTracker
from .api import create_app
from .executor import Executor, KubernetesCluster
from .links import Links
from .projects import LiveProjects
from .queue import PostgresQueue, Queue
from .worker import Workers

log = logging.getLogger("orchestrator")


def migrate(settings: config.Settings) -> int:
    with db.connect(settings.database_url, size=2) as pool:
        db.migrate(pool)
    agent.setup_checkpoints(settings.database_url)
    log.info("the schema is up to date")
    return 0


def executor_of(settings: config.Settings, pool: ConnectionPool, queue: Queue, stop: threading.Event):
    """Agent runs and their watcher, or None while no agent image is configured."""
    if not settings.agent_image:
        log.warning("no agent image (ORCHESTRATOR_COMMIT or agent_image): agent runs are off")
        return None
    executor = Executor(KubernetesCluster(settings.agent_namespace), pool, queue, settings.agent_image,
                        settings.agent_namespace, settings.max_active_runs)
    threading.Thread(target=executor.watch, args=(stop,), name="runs-watcher", daemon=True).start()
    return executor


def serve(settings: config.Settings, host: str, port: int) -> int:
    with ExitStack() as stack:
        pool = stack.enter_context(db.connect(settings.database_url, size=settings.workers + 6))
        missing = db.missing_tables(pool)
        if missing:
            log.error("tables %s are missing: run `orchestrator migrate` first", ", ".join(missing))
            return 1
        checkpointer = stack.enter_context(agent.checkpointer(settings.database_url, settings.workers + 2))
        stop = threading.Event()
        stack.callback(stop.set)
        queue, links = PostgresQueue(pool, settings.max_attempts), Links(pool)
        the_agent = agent.Agent(
            projects=LiveProjects(settings.projects_dir), links=links, queue=queue,
            tracker=PlaneTracker(settings.plane_url, settings.plane_api_key, json.loads(settings.plane_people)),
            code_host=GiteaCodeHost(settings.gitea_url, settings.gitea_token, public_url=settings.gitea_public_url),
            executor=executor_of(settings, pool, queue, stop),
            chat=intents.chat_model(settings.intent_model, settings.llm_base_url, settings.llm_api_key),
            agent_model=settings.agent_model, threshold=settings.intent_threshold,
            checkpointer=checkpointer)
        workers = Workers(queue, the_agent, settings.workers)
        workers.start()
        stack.callback(workers.stop)
        uvicorn.run(create_app(pool, queue, links, the_agent.view), host=host, port=port, log_config=None)
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
