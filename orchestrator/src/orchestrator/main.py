"""The orchestrator's entry point: settings, connections and clients for the agent (agent.py).

orchestrator migrate   bring the schema to the newest revision (a Job before every rollout)
orchestrator serve     the webhooks, the thread API, the workers and the agent runs' watcher
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import AsyncGenerator, Callable
from contextlib import ExitStack, asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from psycopg_pool import ConnectionPool

from . import agent, config, db, intents, llm, logs
from .adapters.gitea import GiteaCodeHost
from .adapters.plane import PlaneTracker, people_of
from .api import create_app
from .executor import Executor, KubernetesCluster
from .links import Links
from .projects import LiveProjects
from .queue import PostgresQueue, Queue
from .worker import Workers

log = logging.getLogger("orchestrator")


def migrate(settings: config.MigrateSettings) -> int:
    database_url = settings.database_url.get_secret_value()
    db.migrate(database_url)
    agent.setup_checkpoints(database_url)
    log.info("the schema is up to date")
    return 0


def executor_of(
    settings: config.Settings, pool: ConnectionPool, queue: Queue, stop: threading.Event
) -> Executor | None:
    """Agent runs and their watcher, or None while no agent image is configured."""
    if not settings.agent_image:
        log.warning("no agent image (ORCHESTRATOR_COMMIT or agent_image): agent runs are off")
        return None
    executor = Executor(
        KubernetesCluster(settings.agent_namespace),
        pool,
        queue,
        settings.agent_image,
        settings.agent_namespace,
        settings.max_active_runs,
    )
    threading.Thread(target=executor.watch, args=(stop,), name="runs-watcher", daemon=True).start()
    return executor


def serve(settings: config.Settings, host: str, port: int) -> int:
    database_url = settings.database_url.get_secret_value()
    problem = db.schema_problem(database_url)
    if problem:
        log.error("%s", problem)
        return 1
    # The stop lives in the app's lifespan: on SIGTERM uvicorn passes the signal on after its
    # shutdown, and what follows uvicorn.run may never run. The stack only closes the pools.
    with ExitStack() as stack:
        pool = stack.enter_context(db.connect(database_url, size=settings.workers + 6))
        checkpointer = stack.enter_context(agent.checkpointer(database_url, settings.workers + 2))
        stop = threading.Event()
        queue, links = PostgresQueue(pool, settings.max_attempts), Links(pool)
        executor = executor_of(settings, pool, queue, stop)
        the_agent = agent.Agent(
            projects=LiveProjects(settings.projects_dir),
            links=links,
            queue=queue,
            tracker=PlaneTracker(
                settings.plane_url,
                settings.plane_api_key.get_secret_value(),
                people_of(settings.plane_people_file).get,
            ),
            code_host=GiteaCodeHost(
                settings.gitea_url, settings.gitea_token.get_secret_value(), settings.gitea_public_url
            ),
            executor=executor,
            chat=llm.chat_model(
                settings.intent_model,
                settings.llm_base_url,
                settings.llm_api_key.get_secret_value(),
                temperature=intents.TEMPERATURE,
            ),
            agent_model=settings.agent_model,
            threshold=settings.intent_threshold,
            checkpointer=checkpointer,
            max_attempts=settings.max_attempts,
        )
        workers = Workers(queue, the_agent, settings.workers)

        @asynccontextmanager
        async def running(app: FastAPI) -> AsyncGenerator[None]:
            """The workers and the run watcher live as long as the app. Stopping them is part of
            uvicorn's shutdown, which runs before it passes SIGTERM on and the process ends."""
            workers.start()
            try:
                yield
            finally:
                stop.set()
                await run_in_threadpool(workers.stop)

        secrets = {
            "plane": settings.plane_webhook_secret.get_secret_value(),
            "gitea": settings.gitea_webhook_secret.get_secret_value(),
        }
        app = create_app(pool, queue, links, the_agent.view, secrets, running)
        uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


# A command: its settings, then what it does with them.
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "migrate": lambda args: migrate(config.load(config.MigrateSettings)),
    "serve": lambda args: serve(config.load(config.Settings), args.host, args.port),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="bring the schema to the newest revision")
    run = commands.add_parser("serve", help="run the service")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    logs.configure()  # before the settings, so a settings error is logged too
    try:
        return COMMANDS[args.command](args)
    except config.ConfigError as error:
        log.error("settings: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
