"""Plane's webhooks as events (payload format from spike 1.3, fixtures in tests/fixtures/plane).

A work item is linked as plane_work_item:<project uuid>/<work item uuid>: Plane's identifiers
can be renamed, its UUIDs cannot. The agent is recognised by its Plane user id, which a mention
in a comment's HTML carries too.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from ..events import Actor, Event, Kind, Source, TrackerItem
from ..http import Api, marker_html
from ..links import Link
from ..live import Live

KIND = "plane_work_item"
MENTION = re.compile(r'<mention-component[^>]*entity_identifier="([0-9a-f-]+)"[^>]*>\s*</mention-component>')
TAG = re.compile(r"<[^>]+>")


# --- What the loop reads of Plane's payloads (spike 1.3); the rest is ignored ------------------


class Someone(BaseModel):
    id: str = ""


class State(BaseModel):
    name: str = ""


class Activity(BaseModel):
    field: str | None = None
    new_value: str | list[str] | None = None
    old_value: str | list[str] | None = None
    actor: Someone = Field(default_factory=Someone)


class Issue(BaseModel):
    id: str = ""
    project: str = ""
    sequence_id: int | None = None
    name: str = ""
    description_stripped: str | None = None
    description_html: str | None = None
    state: State | str | None = None
    assignees: list[Someone | str] = Field(default_factory=list[Someone | str])

    def assigned(self) -> list[str]:
        return [a.id if isinstance(a, Someone) else a for a in self.assignees]


class Comment(BaseModel):
    id: str = ""
    project: str = ""
    issue: str = ""
    actor: str = ""
    comment_html: str = ""


class Payload(BaseModel):
    event: str = ""
    action: str = ""
    workspace_slug: str = ""
    data: dict[str, Any] = Field(default_factory=dict[str, Any])
    activity: Activity = Field(default_factory=Activity)

    @field_validator("data", "activity", mode="before")
    @classmethod
    def null_is_empty(cls, value: object) -> object:
        """What an event does not have may come as null."""
        return {} if value is None else value


def payload(headers: Mapping[str, str], body: dict[str, Any]) -> Payload | None:
    try:
        parsed = Payload.model_validate(body)
    except ValidationError:
        return None
    return parsed.model_copy(update={"event": header(headers, "X-Plane-Event") or parsed.event})


def link_of(headers: Mapping[str, str], body: dict[str, Any]) -> Link | None:
    """The work item an event is about, for routing before anything else is read."""
    parsed = payload(headers, body)
    if parsed is None or parsed.event not in LINKS:
        return None
    try:
        project, item = LINKS[parsed.event](parsed.data)
    except ValidationError:
        return None
    return Link(KIND, f"{project}/{item}") if project and item else None


def issue_of(data: dict[str, Any]) -> tuple[str, str]:
    issue = Issue.model_validate(data)
    return issue.project, issue.id


def commented_issue_of(data: dict[str, Any]) -> tuple[str, str]:
    comment = Comment.model_validate(data)
    return comment.project, comment.issue


# The work item of each kind of webhook: its project and its id.
LINKS: dict[str, Callable[[dict[str, Any]], tuple[str, str]]] = {"issue": issue_of, "issue_comment": commented_issue_of}


def to_event(headers: Mapping[str, str], body: dict[str, Any], agent_id: str) -> Event | None:
    """The event, or None when it is nothing for the loop: the agent's own, or without a request."""
    parsed, link = payload(headers, body), link_of(headers, body)
    if parsed is None or link is None:
        return None
    read = READERS.get(parsed.event)
    return read(parsed, link, agent_id, header(headers, "X-Plane-Delivery") or "") if read else None


def issue_event(p: Payload, link: Link, agent_id: str, delivery: str) -> Event | None:
    """A work item's change: assigned to @agent, or a status a person set. The agent's own are
    dropped."""
    if p.activity.actor.id == agent_id:
        return None
    issue = Issue.model_validate(p.data)
    # Only the id: a display name is the user's to change. The handler finds the person by id.
    who = Actor(Source.PLANE, "", p.activity.actor.id)
    item = TrackerItem(
        workspace=p.workspace_slug,
        project_id=issue.project,
        work_item_id=issue.id,
        sequence_id=issue.sequence_id,
        title=issue.name,
        description=issue.description_stripped or strip(issue.description_html or ""),
    )
    if assigned_now(p, issue, agent_id):
        return Event(Source.PLANE, Kind.ASSIGNED, link, item, who, delivery_id=delivery)
    if p.activity.field == "state":
        state = issue.state.name if isinstance(issue.state, State) else ""
        return Event(Source.PLANE, Kind.STATUS, link, replace(item, state=state), who, delivery_id=delivery)
    return None


def assigned_now(p: Payload, issue: Issue, agent_id: str) -> bool:
    """Created with @agent among its assignees, or @agent added to them."""
    if p.action == "created":
        return agent_id in issue.assigned()
    added, before = p.activity.new_value or [], p.activity.old_value or []
    return p.activity.field == "assignees" and agent_id in added and agent_id not in before


def comment_event(p: Payload, link: Link, agent_id: str, delivery: str) -> Event | None:
    """A new comment that mentions @agent, not by the agent."""
    comment = Comment.model_validate(p.data)
    if p.action != "created" or comment.actor == agent_id:
        return None
    mentioned = [m.group(1) for m in MENTION.finditer(comment.comment_html)]
    if agent_id not in mentioned:
        return None
    text = strip(MENTION.sub(lambda m: "@agent" if m.group(1) == agent_id else "@someone", comment.comment_html))
    item = TrackerItem(workspace=p.workspace_slug, project_id=comment.project, work_item_id=comment.issue)
    who = Actor(Source.PLANE, "", comment.actor)
    return Event(Source.PLANE, Kind.COMMENT, link, item, who, text=text, mentions_agent=True, delivery_id=delivery)


READERS: dict[str, Callable[[Payload, Link, str, str], Event | None]] = {
    "issue": issue_event,
    "issue_comment": comment_event,
}


def strip(markup: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", markup)).split())


def header(headers: Mapping[str, str], name: str) -> str | None:
    lower = {k.lower(): v for k, v in headers.items()}
    return lower.get(name.lower())


# --- The Tracker port on Plane's public API -------------------------------------------------------


def people_of(path: Path, clock: Callable[[], float] = time.monotonic) -> Live[dict[str, str]]:
    """The people's Plane ids (username -> id) from people.json of the Secret sdlc/plane-agent, which
    Plane's setup Job writes and the pod mounts: a person it adds is known without a restart. No
    file, no people: the agent is then found by its key."""

    def read() -> dict[str, str]:
        return PEOPLE.validate_json(path.read_bytes()) if path.exists() else {}

    return Live("the people's Plane ids", lambda: [path] if path.exists() else [], read, clock)


PEOPLE: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])


class PlaneTracker:
    """The agent's side of Plane, with its own API key (Secret sdlc/plane-agent). Plane's public API
    names members by display name only, so people are matched by the ids the setup Job stores."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        people: Callable[[], Mapping[str, str]],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api = Api(
            base_url.rstrip("/") + "/api/v1", {"X-API-Key": api_key}, transport=transport, sleep=sleep, target="plane"
        )
        self.people = people  # username -> Plane user id, as they are now (people_of)
        self._projects: dict[tuple[str, str], str] = {}
        self._states: dict[str, dict[str, str]] = {}

    def every(self, path: str) -> list[dict[str, Any]]:
        """Every item of one of Plane's lists, following its cursor (per_page, next_cursor), so a
        lookup never misses one past the first page."""
        items: list[dict[str, Any]] = []
        params: dict[str, str | int] = {"per_page": PER_PAGE}
        for _ in range(MAX_PAGES):
            page = self.api.json("GET", path, params=params)
            items += results(page)
            if not (isinstance(page, dict) and page.get("next_page_results") and page.get("next_cursor")):
                break
            params = {"per_page": PER_PAGE, "cursor": page["next_cursor"]}
        return items

    def agent_id(self) -> str:
        return self.people().get("agent") or str(self.api.json("GET", "/users/me/")["id"])

    def username(self, user_id: str) -> str:
        return next((name for name, uid in self.people().items() if uid == user_id), "")

    def _load_projects(self, workspace: str) -> None:
        for project in self.every(f"/workspaces/{workspace}/projects/"):
            self._projects[(workspace, project["identifier"])] = project["id"]

    def project_id(self, workspace: str, identifier: str) -> str:
        if (workspace, identifier) not in self._projects:
            self._load_projects(workspace)
        return self._projects[(workspace, identifier)]

    def identifier(self, workspace: str, project_id: str) -> str | None:
        """The identifier (EWA) of a project id, which a webhook carries instead."""

        def find() -> str | None:
            return next(
                (ident for (ws, ident), pid in self._projects.items() if ws == workspace and pid == project_id), None
            )

        if find() is None:
            self._load_projects(workspace)
        return find()

    def _state_ids(self, workspace: str, project_id: str) -> dict[str, str]:
        if project_id not in self._states:
            states = self.every(f"/workspaces/{workspace}/projects/{project_id}/states/")
            self._states[project_id] = {s["name"]: s["id"] for s in states}
        return self._states[project_id]

    def set_status(self, workspace: str, project_id: str, item_id: str, status: str) -> None:
        state = self._state_ids(workspace, project_id)[status]
        path = f"/workspaces/{workspace}/projects/{project_id}/issues/{item_id}/"
        if self.api.json("GET", path).get("state") != state:
            self.api.call("PATCH", path, json={"state": state})

    def comment(self, workspace: str, project_id: str, item_id: str, text: str, marker: str) -> None:
        path = f"/workspaces/{workspace}/projects/{project_id}/issues/{item_id}/comments/"
        note = marker_html(marker)
        if any(note in (c.get("comment_html") or "") for c in self.every(path)):
            return
        paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in text.split("\n") if line.strip())
        self.api.call("POST", path, json={"comment_html": paragraphs + note})


PER_PAGE, MAX_PAGES = 100, 100  # a page of Plane's lists, and how many are read at most


class Page(BaseModel):
    results: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])


def results(page: object) -> list[dict[str, Any]]:
    """The items of a page of Plane's API, paginated (results) or a plain list."""
    return Page.model_validate(page if isinstance(page, dict) else {"results": page or []}).results
