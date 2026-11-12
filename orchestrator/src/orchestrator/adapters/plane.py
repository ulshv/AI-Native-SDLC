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
from typing import Any

import httpx

from ..events import Actor, Event
from ..http import Api, marker_html
from ..links import Link

KIND = "plane_work_item"
MENTION = re.compile(r'<mention-component[^>]*entity_identifier="([0-9a-f-]+)"[^>]*>\s*</mention-component>')
TAG = re.compile(r"<[^>]+>")


def link_of(headers: Mapping[str, str], body: dict[str, Any]) -> Link | None:
    """The work item an event is about, for routing before anything else is read."""
    data = body.get("data") or {}
    event = header(headers, "X-Plane-Event") or body.get("event")
    if event == "issue" and data.get("project") and data.get("id"):
        return Link(KIND, f"{data['project']}/{data['id']}")
    if event == "issue_comment" and data.get("project") and data.get("issue"):
        return Link(KIND, f"{data['project']}/{data['issue']}")
    return None


def to_event(headers: Mapping[str, str], body: dict[str, Any], agent_id: str) -> Event | None:
    """The event, or None when it is nothing for the loop: the agent's own, or without a request."""
    data = body.get("data") or {}
    activity = body.get("activity") or {}
    link = link_of(headers, body)
    delivery = header(headers, "X-Plane-Delivery") or ""
    if link is None:
        return None
    event = header(headers, "X-Plane-Event") or body.get("event")

    if event == "issue":
        actor = activity.get("actor") or {}
        if actor.get("id") == agent_id:
            return None
        # Only the id: a display name is the user's to change. The handler finds the person by id.
        who = Actor("plane", "", actor.get("id", ""))
        common = {
            "workspace": body.get("workspace_slug"),
            "sequence_id": data.get("sequence_id"),
            "project_id": data.get("project"),
            "work_item_id": data.get("id"),
            "title": data.get("name", ""),
            "description": data.get("description_stripped") or strip(data.get("description_html") or ""),
        }
        field = activity.get("field")
        assignees = [a.get("id") if isinstance(a, dict) else a for a in data.get("assignees") or []]
        newly_assigned = (body.get("action") == "created" and agent_id in assignees) or (
            field == "assignees"
            and agent_id in (activity.get("new_value") or [])
            and agent_id not in (activity.get("old_value") or [])
        )
        if newly_assigned:
            return Event("plane", "assigned", link, who, data=common, delivery_id=delivery)
        if field == "state":
            state = data.get("state") or {}
            return Event(
                "plane",
                "status",
                link,
                who,
                data={**common, "state": state.get("name", "") if isinstance(state, dict) else ""},
                delivery_id=delivery,
            )
        return None

    if event == "issue_comment" and body.get("action") == "created":
        if data.get("actor") == agent_id:
            return None
        markup = data.get("comment_html") or ""
        mentioned = [m.group(1) for m in MENTION.finditer(markup)]
        if agent_id not in mentioned:
            return None
        text = strip(MENTION.sub(lambda m: "@agent" if m.group(1) == agent_id else "@someone", markup))
        return Event(
            "plane",
            "comment",
            link,
            Actor("plane", "", data.get("actor", "")),
            text=text,
            mentions_agent=True,
            data={
                "workspace": body.get("workspace_slug"),
                "comment_id": data.get("id"),
                "work_item_id": data.get("issue"),
                "project_id": data.get("project"),
            },
            delivery_id=delivery,
        )
    return None


def strip(markup: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", markup)).split())


def header(headers: Mapping[str, str], name: str) -> str | None:
    lower = {k.lower(): v for k, v in headers.items()}
    return lower.get(name.lower())


# --- The Tracker port on Plane's public API -------------------------------------------------------


class PlaneTracker:
    """The agent's side of Plane, with its own API key (Secret sdlc/plane-agent). Plane's public API
    names members by display name only, so people are matched by the ids the setup Job stores."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        people: dict[str, str],
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api = Api(
            base_url.rstrip("/") + "/api/v1", {"X-API-Key": api_key}, transport=transport, sleep=sleep, target="plane"
        )
        self.people = people
        self._projects: dict[tuple[str, str], str] = {}
        self._states: dict[str, dict[str, str]] = {}

    def agent_id(self) -> str:
        return self.people.get("agent") or str(self.api.json("GET", "/users/me/")["id"])

    def username(self, user_id: str) -> str:
        return next((name for name, uid in self.people.items() if uid == user_id), "")

    def _load_projects(self, workspace: str) -> None:
        for project in results(self.api.json("GET", f"/workspaces/{workspace}/projects/")):
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
            states = results(self.api.json("GET", f"/workspaces/{workspace}/projects/{project_id}/states/"))
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
        if any(note in (c.get("comment_html") or "") for c in results(self.api.json("GET", path))):
            return
        paragraphs = "".join(f"<p>{html.escape(line)}</p>" for line in text.split("\n") if line.strip())
        self.api.call("POST", path, json={"comment_html": paragraphs + note})


def results(page: Any) -> list[dict[str, Any]]:
    """The items of a page of Plane's API, paginated or not."""
    items = page.get("results", []) if isinstance(page, dict) else page
    return list(items or [])
