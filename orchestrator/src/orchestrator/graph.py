"""Threads (design decision 4): one LangGraph thread per work item, its state in Postgres.

The dispatcher turns a delivery into a command for a thread: it normalises the payload, finds the
connected project, finds the thread through thread_link (or starts one when a ticket is assigned
to @agent), resolves the person and their roles, and reads the request (an explicit signal, or
one model call). The graph then runs one step: decide (loop.py) and act, in a single node, so a
step interrupted halfway runs again from the state before it, with the same run names and
comment markers. No interrupt(): every event runs to the end (D21).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import intents, loop
from .adapters import gitea, plane
from .events import Event
from .executor import Run
from .links import Link, Links
from .loop import run_name
from .policy import Phase, signal
from .projects import Project, Projects
from .queue import Delivery, Queue

log = logging.getLogger(__name__)
GITEA_PUBLIC = "https://gitea.sdlc.internal"


class Thread(TypedDict, total=False):
    item: dict  # the work item: phase, key, branch, change, pr, answers, messages, applied, ...
    command: dict  # this step's input


@dataclass
class Services:
    projects: Projects
    links: Links
    queue: Queue
    tracker: Any  # Tracker port
    code_host: Any  # CodeHost port
    executor: Any  # Executor, or None while agent runs are off
    chat: Any  # chat model for intents
    agent_model: str
    threshold: float = 0.7


def project_dict(project: Project) -> dict:
    return {"name": project.name, "identifier": project.identifier, "base": project.base}


# --- act -----------------------------------------------------------------------------------------

def act(item: dict, actions: list[loop.Action], command: dict, svc: Services, thread_id: str) -> dict:
    """Takes the step's actions in order, each safe to repeat."""
    project = svc.projects.by_tracker(item["workspace"], item["key"].rsplit("-", 1)[0])
    delivery = command["delivery"]
    pr_url = f"{GITEA_PUBLIC}/{project.repo}/pulls/{item['pr']}" if item.get("pr") else ""

    def text_of(action):
        return action.text.replace("{pr_url}", pr_url)

    for n, action in enumerate(actions):
        marker = f"{delivery}/{n}"
        if action.kind == "status":
            svc.tracker.set_status(item["workspace"], item["project_id"], item["work_item_id"], action.text)
        elif action.kind == "ticket" or (action.kind == "reply" and command.get("source") != "gitea"):
            svc.tracker.comment(item["workspace"], item["project_id"], item["work_item_id"], text_of(action), marker)
            item = with_message(item, "agent", text_of(action))
        elif action.kind in ("pr", "reply"):
            if item.get("pr"):
                svc.code_host.comment(project.repo, item["pr"], text_of(action), marker)
            else:  # no pull request yet: the ticket is the place
                svc.tracker.comment(item["workspace"], item["project_id"], item["work_item_id"], text_of(action), marker)
            item = with_message(item, "agent", text_of(action))
        elif action.kind == "open_pr":
            head = action.extra.get("head") or item["branch"]
            base = action.extra.get("base") or project.base
            title = action.extra.get("title") or f"{item['key']}: {item['title']}"
            number = svc.code_host.find_or_open_pr(project.repo, head, base, title,
                                                   f"Ticket {item['key']}: {item['title']}\n\nOpened by @agent.")
            svc.links.link(thread_id, Link(gitea.KIND, f"{project.repo}#{number}"))
            if head == item["branch"]:
                item = {**item, "pr": number}
            pr_url = f"{GITEA_PUBLIC}/{project.repo}/pulls/{number}"
        elif action.kind == "label":
            svc.code_host.label(project.repo, item["pr"], action.text)
        elif action.kind == "merge":
            svc.code_host.merge(project.repo, item["pr"])
        elif action.kind == "close_pr":
            svc.code_host.close(project.repo, item["pr"])
        elif action.kind == "run":
            if svc.executor is None:
                raise RuntimeError("agent runs are off: no agent image configured")
            branch = action.extra.get("branch") or item["branch"]
            svc.executor.start(Run(run_name(item["key"], action.mode, item["runs"]), thread_id, {
                "mode": action.mode, "repo": project.repo, "base": project.base, "branch": branch,
                "change": item["change"], "model": svc.agent_model, "checks": list(project.checks),
                "ticket": {"key": item["key"], "title": item["title"], "text": item.get("text", "")},
                "answers": item.get("answers", []),
                **({"feedback": item["feedback"]} if item.get("feedback") else {}),
                **({"unarchive": True} if action.extra.get("unarchive") else {})}))
    return item


def with_message(item: dict, who: str, text: str) -> dict:
    return {**item, "messages": [*(item.get("messages") or []), {"from": who, "text": text}]}


# --- the graph -----------------------------------------------------------------------------------

def build(svc: Services, checkpointer):
    def step(state: Thread, config) -> Thread:
        item, command = dict(state.get("item") or {}), state["command"]
        thread_id = config["configurable"]["thread_id"]
        if loop.applied(item, command["delivery"]):
            log.info("delivery %s was applied to thread %s before", command["delivery"], thread_id)
            return {"item": item}
        if command.get("from"):
            item = with_message(item, command["from"], command.get("text", ""))
        kind = command["kind"]
        project = svc.projects.by_tracker(item["workspace"], item["key"].rsplit("-", 1)[0]) if item.get("key") else None
        if kind == "intake":
            decided = loop.intake(item, command["event"], command["project"])
        elif kind == "request":
            roles = None if command.get("roles") is None else frozenset(command["roles"])
            decided = loop.on_request(item, command["intent"], command.get("text", ""), roles,
                                      command.get("source", ""), command.get("feedback", []),
                                      command.get("status_signal", False))
        elif kind == "run":
            if not loop.current(item, command.get("job", "")):
                log.info("result of run %s dropped: thread %s waits for %r", command.get("job"), thread_id,
                         item.get("running") or "no run")
            decided = loop.on_run(item, command.get("job", ""), command["result"], project.previews if project else False)
        elif kind == "pr":
            decided = loop.on_pr(item, command["event_kind"])
        else:
            raise ValueError(f"unknown command {kind}")
        new = act(decided.state, decided.actions, command, svc, thread_id)
        return {"item": loop.remember(new, command["delivery"])}

    graph = StateGraph(Thread)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    return graph.compile(checkpointer=checkpointer)


# --- the dispatcher --------------------------------------------------------------------------------

class Dispatcher:
    """The queue's handler: a delivery in, at most one thread step out."""

    def __init__(self, svc: Services, graph):
        self.svc, self.graph = svc, graph

    def item(self, thread_id: str) -> dict:
        snapshot = self.graph.get_state({"configurable": {"thread_id": thread_id}})
        return dict((snapshot.values or {}).get("item") or {})

    def run(self, thread_id: str, command: dict) -> None:
        self.graph.invoke({"command": command}, {"configurable": {"thread_id": thread_id}})

    def __call__(self, delivery: Delivery) -> None:
        if delivery.source == "job":
            payload = delivery.payload
            self.run(payload["thread_id"], {"kind": "run", "delivery": f"job/{delivery.delivery_id}",
                                            "job": payload.get("job", ""), "result": payload.get("result") or {}})
            return
        event = self.event_of(delivery)
        if event is None:
            return
        project = self.svc.projects.of_event(event, self.svc.tracker.identifier)
        if project is None:
            log.info("%s event of an unconnected project or repository: left alone", delivery.source)
            return
        if event.kind == "release":
            return  # releases come with group 10
        thread_id = self.svc.links.thread_of(event.link)
        delivery_key = f"{delivery.source}/{delivery.delivery_id}"
        if thread_id is None:
            if event.kind != "assigned":
                log.info("%s on %s, which no thread works on: left alone", event.kind, event.link)
                return
            thread_id = str(uuid.uuid4())
            self.svc.links.link(thread_id, event.link)
        if event.kind == "assigned":  # again on a retry, should the first try have stopped here
            self.svc.links.link(thread_id, Link("ticket", f"{project.name}/{project.identifier}-{event.data['sequence_id']}"))
        if delivery.routing_key != thread_id:
            # The object got its thread after this event was recorded: its waiting events follow.
            self.svc.queue.rekey(delivery.routing_key, thread_id)
        item = self.item(thread_id)
        if loop.applied(item, delivery_key):
            return  # a replay: the graph would skip it too, and nothing needs reading
        if event.kind == "assigned":
            if item.get("phase") and item.get("phase") != Phase.CLOSED:
                return  # assigned again while the work goes on
            self.run(thread_id, {"kind": "intake", "delivery": delivery_key, "event": {"data": event.data},
                                 "project": project_dict(project)})
            return
        if event.kind in ("pr_merged", "pr_closed"):
            self.run(thread_id, {"kind": "pr", "delivery": delivery_key, "event_kind": event.kind})
            return
        person = self.person(project, event)
        intent = self.intent(event, item, person is not None)
        if intent is None:
            return
        feedback = []
        if event.kind == "review" and intent == "request_changes":
            feedback = self.svc.code_host.review_comments(project.repo, event.data["number"], event.actor.username)
        self.run(thread_id, {"kind": "request", "delivery": delivery_key, "intent": intent, "text": event.text,
                             "roles": sorted(person.roles) if person else None, "source": event.source,
                             "feedback": feedback, "status_signal": event.kind == "status", "from": person.name if person else (event.actor.username or "someone")})

    def event_of(self, delivery: Delivery) -> Event | None:
        if delivery.source == "plane":
            return plane.to_event(delivery.headers, delivery.payload, self.svc.tracker.agent_id())
        if delivery.source == "gitea":
            return gitea.to_event(delivery.headers, delivery.payload)
        return None

    def person(self, project: Project, event: Event):
        if event.actor is None:
            return None
        username = event.actor.username if event.actor.tool == "gitea" else self.svc.tracker.username(event.actor.id)
        return project.person(event.actor.tool, username)

    # A reading may make an explicit signal more precise, never turn it around: an approving review
    # whose text asks to merge is a request to merge; a review requesting changes stays one.
    REFINES = {"approve": {"merge"}}

    def intent(self, event: Event, item: dict, known: bool) -> str | None:
        """An explicit signal needs no model; a mention is read by one; nothing else is a request.
        No model reads a stranger's comment (the policy refuses it whatever it says), nor one in
        Needs info, where the next mention is the answer."""
        explicit = signal(event.kind, event.data)
        if not (event.mentions_agent and event.text):
            return explicit
        if not known:
            return explicit or "unclear"
        if not explicit and item.get("phase") == Phase.NEEDS_INFO:
            return "answer"
        last = next((m["text"] for m in reversed(item.get("messages") or []) if m["from"] == "agent"), "")
        reading = intents.read(self.svc.chat, event.text, item.get("phase", ""), last)
        if explicit:
            return reading.name if intents.clear(reading, self.svc.threshold) and \
                reading.name in self.REFINES.get(explicit, ()) else explicit
        return reading.name if intents.clear(reading, self.svc.threshold) else "unclear"
