"""The ports the core talks through (design decision 6): a tracker and a code host. Plane and
Gitea implement them today; another tracker, a chat or a tool behind MCP can implement them too.
"""

from __future__ import annotations

from typing import Protocol

from .executor import Run
from .links import Link


class Tracker(Protocol):
    def agent_id(self) -> str:
        """The agent's own user id, to recognise its events and mentions."""

    def username(self, user_id: str) -> str:
        """The username of a user id, for matching people."""

    def project_id(self, workspace: str, identifier: str) -> str:
        """The stable id of a tracker project named by its identifier, such as EWA."""

    def identifier(self, workspace: str, project_id: str) -> str | None:
        """The identifier (EWA) of a tracker project's id, which its webhooks carry instead."""

    def set_status(self, workspace: str, project_id: str, item_id: str, status: str) -> None: ...

    def comment(self, workspace: str, project_id: str, item_id: str, text: str, marker: str) -> None:
        """Comments once: a comment carrying the marker already is not written again."""


class CodeHost(Protocol):
    def find_or_open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> int: ...

    def pr_link(self, repo: str, number: int) -> Link:
        """The thread link of a pull request, as the code host's events name it."""

    def pr_url(self, repo: str, number: int) -> str:
        """The pull request's address for people."""

    def comment(self, repo: str, number: int, text: str, marker: str) -> None: ...

    def label(self, repo: str, number: int, label: str) -> None: ...

    def merge(self, repo: str, number: int) -> None: ...

    def close(self, repo: str, number: int) -> None: ...

    def review_comments(self, repo: str, number: int, reviewer: str) -> list[str]:
        """The latest review's inline comments by that person, which the webhook leaves out."""


class RunStarter(Protocol):
    def start(self, run: Run) -> str:
        """Starts the run, or queues it while others go; starting it again finds the first try's."""
