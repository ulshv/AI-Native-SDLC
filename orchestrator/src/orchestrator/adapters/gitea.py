"""Gitea's webhooks as events (payload format from spike 1.4, fixtures in tests/fixtures/gitea).

A pull request is linked as gitea_pr:<owner/repo>#<number>. Mentions are the literal @agent. A
review's event carries its text but neither its id nor its inline comments: the handler reads
those through the API when it needs them (design decision 6).
"""

# The payloads are raw JSON (dict[str, Any]) until task 7.23 parses them into typed models; until
# then, what is read from them is not tracked as unknown.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from ..events import Actor, Event
from ..http import Api, ApiError, marker_html
from ..links import Link
from .plane import header

KIND = "gitea_pr"
PAGE, MAX_PAGES = 50, 100  # a page of Gitea's lists, and how many are read at most
AGENT_ACCOUNTS = {"agent", "agent-runner"}
MENTION = re.compile(r"(?<![\w@])@agent(?![\w-])")
REVIEWS = {
    "pull_request_review_approved": "approved",
    "pull_request_review_rejected": "request_changes",
    "pull_request_review_comment": "comment",
}


def pr_link(repo: str, number: int) -> Link:
    """A pull request's thread link, as the webhooks and the agent name it."""
    return Link(KIND, f"{repo}#{number}")


def link_of(headers: Mapping[str, str], body: dict[str, Any]) -> Link | None:
    repo = (body.get("repository") or {}).get("full_name")
    number = body.get("number") or (body.get("pull_request") or {}).get("number")
    if header(headers, "X-Gitea-Event") == "issue_comment":
        issue = body.get("issue") or {}
        if not (body.get("is_pull") or issue.get("pull_request")):
            return None
        number = issue.get("number")
    if header(headers, "X-Gitea-Event") == "release" or not repo or not number:
        return None
    return pr_link(repo, number)


def to_event(headers: Mapping[str, str], body: dict[str, Any]) -> Event | None:
    sender = (body.get("sender") or {}).get("login", "")
    if sender in AGENT_ACCOUNTS:
        return None
    who = Actor("gitea", sender)
    delivery = header(headers, "X-Gitea-Delivery") or ""
    kind = header(headers, "X-Gitea-Event-Type") or header(headers, "X-Gitea-Event")
    repo = (body.get("repository") or {}).get("full_name", "")

    if kind == "release" and body.get("action") == "published":
        release = body.get("release") or {}
        tickets = re.findall(r"\b[A-Z][A-Z0-9]+-[0-9]+\b", release.get("body") or "")
        return Event(
            "gitea",
            "release",
            Link("gitea_release", f"{repo}@{release.get('tag_name')}"),
            who,
            data={"repo": repo, "tag": release.get("tag_name"), "tickets": tickets},
            delivery_id=delivery,
        )

    link = link_of(headers, body)
    if link is None:
        return None
    pr = body.get("pull_request") or {}
    common = {
        "repo": repo,
        "number": int(link.ref.rsplit("#", 1)[1]),
        "head": (pr.get("head") or {}).get("ref"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "base": (pr.get("base") or {}).get("ref"),
    }

    if kind in ("pull_request", "pull_request_sync"):
        action = body.get("action")
        if action == "closed":
            return Event(
                "gitea", "pr_merged" if pr.get("merged") else "pr_closed", link, who, data=common, delivery_id=delivery
            )
        if action == "opened":
            return Event("gitea", "pr_opened", link, who, data=common, delivery_id=delivery)
        return None

    # Only a new comment is a request: an edited or deleted one would repeat or revive it.
    if kind == "pull_request_comment" and header(headers, "X-Gitea-Event") == "issue_comment":
        if body.get("action") != "created":
            return None
        text = (body.get("comment") or {}).get("body", "")
        if not MENTION.search(text):
            return None
        return Event(
            "gitea",
            "comment",
            link,
            who,
            text=text,
            mentions_agent=True,
            data={**common, "comment_id": (body.get("comment") or {}).get("id")},
            delivery_id=delivery,
        )

    if kind in REVIEWS:
        review = REVIEWS[kind]
        text = (body.get("review") or {}).get("content", "")
        mentioned = bool(MENTION.search(text))
        # An approval or a request for changes is a signal on its own; a plain review comment
        # counts only when it mentions the agent.
        if review == "comment" and not mentioned:
            return None
        return Event(
            "gitea",
            "review",
            link,
            who,
            text=text,
            mentions_agent=mentioned,
            data={**common, "review": review},
            delivery_id=delivery,
        )
    return None


# --- The CodeHost port on Gitea's API, as agent -------------------------------------------------


class GiteaCodeHost:
    """The orchestrator's side of Gitea, with agent's token (Secret sdlc/gitea-agent). Gitea's
    branch protection decides what agent may merge (design decision 8)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        public_url: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        headers = {"Authorization": f"token {token}"}
        self.api = Api(base_url.rstrip("/") + "/api/v1", headers, transport=transport, sleep=sleep, target="gitea")
        # Payloads' html_url follow the host a request came through, so addresses for people are
        # built from this.
        self.public_url = public_url.rstrip("/")

    def pr_link(self, repo: str, number: int) -> Link:
        return pr_link(repo, number)

    def pr_url(self, repo: str, number: int) -> str:
        return f"{self.public_url}/{repo}/pulls/{number}"

    def every(self, path: str, **params: str) -> list[dict[str, Any]]:
        """Every item of one of Gitea's paged lists (page, limit), so a lookup never misses one
        past the first page."""
        items: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            batch = self.api.json("GET", path, params={**params, "limit": PAGE, "page": page}) or []
            items += batch
            if len(batch) < PAGE:
                break
        return items

    def open_pr(self, repo: str, head: str, base: str) -> int | None:
        """The open pull request from head into base, if there is one."""
        for pr in self.every(f"/repos/{repo}/pulls", state="open"):
            if (pr.get("head") or {}).get("ref") == head and (pr.get("base") or {}).get("ref") == base:
                return int(pr["number"])
        return None

    def find_or_open_pr(self, repo: str, head: str, base: str, title: str, body: str) -> int:
        """The open pull request of the branch, opened when there is none. A closed one of the same
        branch does not count; one opened meanwhile (409) is found again."""
        if (number := self.open_pr(repo, head, base)) is not None:
            return number
        try:
            return int(
                self.api.json(
                    "POST", f"/repos/{repo}/pulls", json={"head": head, "base": base, "title": title, "body": body}
                )["number"]
            )
        except ApiError as error:
            if error.status == 409 and (number := self.open_pr(repo, head, base)) is not None:
                return number
            raise

    def comment(self, repo: str, number: int, text: str, marker: str) -> None:
        note = marker_html(marker)
        comments = self.api.json("GET", f"/repos/{repo}/issues/{number}/comments")  # all of them: not paged
        if any(note in (c.get("body") or "") for c in comments or []):
            return
        self.api.call("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": f"{text}\n\n{note}"})

    def label(self, repo: str, number: int, label: str) -> None:
        labels = {entry["name"]: entry["id"] for entry in self.every(f"/repos/{repo}/labels")}
        if label not in labels:
            labels[label] = self.api.json("POST", f"/repos/{repo}/labels", json={"name": label, "color": "#0ea5e9"})[
                "id"
            ]
        self.api.call("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": [labels[label]]})

    def merged(self, repo: str, number: int) -> bool:
        return bool(self.api.json("GET", f"/repos/{repo}/pulls/{number}").get("merged"))

    def merge(self, repo: str, number: int) -> None:
        if self.merged(repo, number):
            return
        # Gitea's merge commit reads "Merge pull request '<title>' (#n) from <head> into <base>",
        # which the release workflow reads the ticket keys from.
        self.api.call("POST", f"/repos/{repo}/pulls/{number}/merge", json={"Do": "merge"})

    def close(self, repo: str, number: int) -> None:
        if self.api.json("GET", f"/repos/{repo}/pulls/{number}").get("state") != "closed":
            self.api.call("PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})

    def review_comments(self, repo: str, number: int, reviewer: str) -> list[str]:
        reviews = [
            r
            for r in self.every(f"/repos/{repo}/pulls/{number}/reviews")
            if (r.get("user") or {}).get("login") == reviewer
        ]
        if not reviews:
            return []
        latest = max(reviews, key=lambda r: r["id"])
        comments = self.api.json("GET", f"/repos/{repo}/pulls/{number}/reviews/{latest['id']}/comments") or []
        return [
            f"{c.get('path')}:{c.get('position') or c.get('original_position') or ''}: {c.get('body')}"
            for c in comments
        ]
