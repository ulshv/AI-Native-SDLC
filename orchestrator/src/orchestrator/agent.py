"""The agent's root (design decision 4): its LangGraph threads, their checkpoints in Postgres,
and the parts it is made of.

One thread per work item. A thread runs one step per command in a single node: the loop's rules
(machine.py) act through the ports and return the new item, which the checkpoint keeps. A step
interrupted halfway runs again from the item before it; a delivery applied before is skipped.
No interrupt(): every event runs to the end (D21).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import machine
from .dispatch import Dispatcher
from .links import Links
from .model import COMMANDS, Command, Intake, WorkItem
from .ports import CodeHost, Tracker
from .projects import Projects
from .queue import Delivery, Queue

log = logging.getLogger(__name__)


def setup_checkpoints(database_url: str) -> None:
    """LangGraph's checkpoint tables; part of `orchestrator migrate`."""
    with PostgresSaver.from_conn_string(database_url) as saver:
        saver.setup()


@contextmanager
def checkpointer(database_url: str, size: int) -> Iterator[PostgresSaver]:
    """A checkpointer on a pool of its own, in the shape PostgresSaver needs."""
    with ConnectionPool(database_url, min_size=1, max_size=size,
                        kwargs={"autocommit": True, "row_factory": dict_row}, open=True) as pool:
        yield PostgresSaver(pool)


class State(TypedDict, total=False):
    item: dict  # WorkItem as JSON
    command: dict  # Command as JSON


class Agent:
    """The queue's handler (a delivery in, at most one thread step out) and the threads it keeps."""

    def __init__(self, projects: Projects, links: Links, queue: Queue, tracker: Tracker, code_host: CodeHost,
                 executor, chat, agent_model: str, threshold: float, checkpointer):
        self.projects = projects
        self.ports = machine.Ports(tracker, code_host, links, executor, agent_model)
        self.dispatcher = Dispatcher(projects, links, queue, tracker, code_host, chat, threshold, self)
        graph = StateGraph(State)
        graph.add_node("step", self._step)
        graph.add_edge(START, "step")
        graph.add_edge("step", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    def __call__(self, delivery: Delivery) -> None:
        self.dispatcher(delivery)

    def _step(self, state: State, config) -> State:
        thread_id = config["configurable"]["thread_id"]
        item = WorkItem.model_validate(state.get("item") or {})
        command = COMMANDS.validate_python(state["command"])
        if item.applied_before(command.delivery):
            log.info("delivery %s was applied to thread %s before", command.delivery, thread_id)
            return {"item": item.model_dump(mode="json")}
        if item.ticket is None and not isinstance(command, Intake):
            # The intake never went through (its delivery failed): there is no item to act on.
            log.info("thread %s has no work item yet: %s left alone", thread_id, command.kind)
            return {"item": item.model_dump(mode="json")}
        project = self.projects.by_name(item.project or command.project)
        if project is None:
            raise LookupError(f"thread {thread_id} belongs to no connected project ({item.project or command.project!r})")
        new = machine.advance(item, command, project, self.ports, thread_id)
        new.remember(command.delivery)
        return {"item": new.model_dump(mode="json")}

    def run(self, thread_id: str, command: Command) -> None:
        self.graph.invoke({"command": command.model_dump(mode="json")}, {"configurable": {"thread_id": thread_id}})

    def item(self, thread_id: str) -> WorkItem:
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return WorkItem.model_validate((snapshot.values or {}).get("item") or {})

    def view(self, thread_id: str) -> dict:
        """What the thread API shows: the item's summary and its conversation; {} for no item."""
        item = self.item(thread_id)
        if item.ticket is None:
            return {}
        return {"values": item.summary(), "messages": [m.model_dump() for m in item.messages]}
