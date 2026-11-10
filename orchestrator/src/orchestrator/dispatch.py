"""From a delivery in the queue to the command of a thread's step: the event (through the
source's adapter), its connected project, its thread (a ticket assigned to @agent starts one),
and what the event means for the item; a person's request is read from an explicit signal or
with one model call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol

from pydantic import ValidationError

from . import intents
from .adapters import gitea, plane
from .events import Event
from .links import Link, Links
from .model import Command, Intake, PrChanged, Request, RunFinished, RunResult, Where, WorkItem
from .policy import Phase, signal
from .ports import CodeHost, Tracker
from .projects import Project, Projects
from .queue import Delivery, Queue

log = logging.getLogger(__name__)

IGNORED = {"release"}  # releases come with group 10
VIA = {"plane": Where.TICKET, "gitea": Where.PR}  # where a request was made, and its answer goes
# A reading may make an explicit signal more precise, never turn it around: an approving review
# that asks to merge is a merge request, a review requesting changes stays one.
REFINES = {"approve": {"merge"}}


def read_intent(chat, threshold: float, event: Event, item: WorkItem, known: bool) -> str | None:
    """An explicit signal needs no model; a mention is read by one; nothing else is a request. No
    model reads a stranger's comment (the policy refuses it whatever it says), nor one in Needs
    info, where the next mention is the answer."""
    explicit = signal(event.kind, event.data)
    if not (event.mentions_agent and event.text):
        return explicit
    if not known:
        return explicit or "unclear"
    if not explicit and item.phase == Phase.NEEDS_INFO:
        return "answer"
    reading = intents.read(chat, event.text, item.phase or "", item.last_said())
    clear = intents.clear(reading, threshold)
    if explicit:
        return reading.name if clear and reading.name in REFINES.get(explicit, ()) else explicit
    return reading.name if clear else "unclear"


def result_of(payload: dict) -> RunResult:
    """The run's termination message, which the agent's run writes: one not in the shape asked
    for counts as a failed run, so the item does not wait for it forever."""
    try:
        return RunResult.model_validate(payload.get("result") or {})
    except ValidationError as error:
        return RunResult(result="failed", reason=f"the run's result has no usable shape: {error.errors()[0]['msg']}")


class Threads(Protocol):
    def item(self, thread_id: str) -> WorkItem: ...

    def run(self, thread_id: str, command: Command) -> None: ...


class Dispatcher:
    """The queue's handler: a delivery in, at most one thread step out."""

    def __init__(self, projects: Projects, links: Links, queue: Queue, tracker: Tracker, code_host: CodeHost,
                 chat, threshold: float, threads: Threads):
        self.projects, self.links, self.queue = projects, links, queue
        self.tracker, self.code_host, self.chat, self.threshold = tracker, code_host, chat, threshold
        self.threads = threads
        self.adapters: dict[str, Callable[[Delivery], Event | None]] = {
            "plane": lambda d: plane.to_event(d.headers, d.payload, tracker.agent_id()),
            "gitea": lambda d: gitea.to_event(d.headers, d.payload),
        }
        self.commands: dict[str, Callable[[str, Event, Project, WorkItem], Command | None]] = {
            "assigned": self._intake,
            "pr_merged": lambda delivery, event, project, item: PrChanged(delivery=delivery, merged=True),
            "pr_closed": lambda delivery, event, project, item: PrChanged(delivery=delivery, merged=False),
        }

    def __call__(self, delivery: Delivery) -> None:
        if delivery.source == "job":  # an agent run's result names its thread itself
            payload = delivery.payload
            self.threads.run(payload["thread_id"], RunFinished(
                delivery=f"job/{delivery.delivery_id}", job=payload.get("job", ""), result=result_of(payload)))
            return
        adapter = self.adapters.get(delivery.source)
        event = adapter(delivery) if adapter else None
        if event is None or event.kind in IGNORED:
            return
        project = self.projects.of_event(event, self.tracker.identifier)
        if project is None:
            log.info("%s event of an unconnected project or repository: left alone", delivery.source)
            return
        thread_id = self._thread_of(event, project)
        if thread_id is None:
            log.info("%s on %s, which no thread works on: left alone", event.kind, event.link)
            return
        if delivery.routing_key != thread_id:
            # The object got its thread after this event was recorded: its waiting events follow.
            self.queue.rekey(delivery.routing_key, thread_id)
        key, item = f"{delivery.source}/{delivery.delivery_id}", self.threads.item(thread_id)
        if item.applied_before(key):
            return  # a replay: nothing needs reading
        command = self.commands.get(event.kind, self._request)(key, event, project, item)
        if command is not None:
            self.threads.run(thread_id, command)

    def _thread_of(self, event: Event, project: Project) -> str | None:
        """The event's thread; a ticket assigned to @agent gets one, linked to the work item and
        to its key (again on a retry, should the first try have stopped in between)."""
        thread_id = self.links.thread_of(event.link)
        if event.kind != "assigned":
            return thread_id
        thread_id = thread_id or str(uuid.uuid4())
        self.links.link(thread_id, event.link)
        self.links.link(thread_id, Link("ticket", f"{project.name}/{project.identifier}-{event.data['sequence_id']}"))
        return thread_id

    def _intake(self, delivery: str, event: Event, project: Project, item: WorkItem) -> Intake:
        data = event.data
        return Intake(delivery=delivery, project=project.name, sequence_id=data["sequence_id"],
                      title=data.get("title", ""), description=data.get("description", ""),
                      workspace=data.get("workspace", ""), project_id=data.get("project_id", ""),
                      work_item_id=data.get("work_item_id", ""))

    def _request(self, delivery: str, event: Event, project: Project, item: WorkItem) -> Request | None:
        person = self._person(project, event)
        intent = read_intent(self.chat, self.threshold, event, item, person is not None)
        if intent is None:
            return None
        feedback = []
        if event.kind == "review" and intent == "request_changes":
            feedback = self.code_host.review_comments(project.repo, event.data["number"], event.actor.username)
        return Request(delivery=delivery, project=project.name, intent=intent, text=event.text,
                       author=person.name if person else (event.actor.username if event.actor else "") or "someone",
                       roles=person.roles if person else None, via=VIA[event.source], feedback=feedback,
                       status_signal=event.kind == "status")

    def _person(self, project: Project, event: Event):
        if event.actor is None:
            return None
        username = event.actor.username if event.actor.tool == "gitea" else self.tracker.username(event.actor.id)
        return project.person(event.actor.tool, username)
