"""Events: what an adapter makes of a raw webhook (design decision 6). The core works with these
and never with a tracker's or a git server's payloads, so another source (another tracker, a
chat, a tool reached through MCP) is one more adapter. What an event carries depends on what it
is about: a tracker's work item, a pull request or a release.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .links import Link


class Source(StrEnum):
    PLANE = "plane"
    GITEA = "gitea"


class Kind(StrEnum):
    ASSIGNED = "assigned"  # a ticket assigned to @agent
    STATUS = "status"  # a person set a ticket's status
    COMMENT = "comment"  # a comment mentioning @agent
    REVIEW = "review"  # a review's verdict, or a review comment mentioning @agent
    PR_OPENED = "pr_opened"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    RELEASE = "release"


@dataclass(frozen=True)
class Actor:
    """Who did it, as the source names them: the tool and the username there."""

    tool: Source
    username: str
    id: str = ""


@dataclass(frozen=True)
class TrackerItem:
    """A tracker's work item, as the event names it."""

    workspace: str
    project_id: str
    work_item_id: str
    sequence_id: int | None = None
    title: str = ""
    description: str = ""
    state: str = ""  # a status event's new status


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    head: str = ""
    head_sha: str = ""
    base: str = ""
    review: str = ""  # a review event's verdict: approved | request_changes | comment


@dataclass(frozen=True)
class Release:
    repo: str
    tag: str
    tickets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    source: Source
    kind: Kind
    link: Link
    about: TrackerItem | PullRequest | Release
    actor: Actor | None = None
    text: str = ""  # a comment's or a review's words
    mentions_agent: bool = False
    delivery_id: str = ""
