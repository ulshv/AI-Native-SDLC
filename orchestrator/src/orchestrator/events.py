"""Events: what an adapter makes of a raw webhook or a finished agent run (design decision 6).

The core works with these and never with a tracker's or a git server's payloads, so another
source (another tracker, a chat, a tool reached through MCP) is one more adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .links import Link


@dataclass(frozen=True)
class Actor:
    """Who did it, as the source names them: the tool and the username there."""

    tool: str  # plane | gitea
    username: str
    id: str = ""


@dataclass(frozen=True)
class Event:
    source: str  # plane | gitea | job
    # assigned | status | comment | review | pr_opened | pr_merged | pr_closed | release | run_finished
    kind: str
    link: Link | None
    actor: Actor | None = None
    text: str = ""  # a comment's or a review's words
    mentions_agent: bool = False
    data: dict[str, Any] = field(default_factory=dict)  # by kind: state, review, merged, tag, tickets, result, ...
    delivery_id: str = ""
