"""Gitea's webhooks as events (payload format from spike 1.4, fixtures in tests/fixtures/gitea).

A pull request is linked as gitea_pr:<owner/repo>#<number>. Mentions are the literal @agent. A
review's event carries its text but neither its id nor its inline comments: the handler reads
those through the API when it needs them (design decision 6).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..events import Actor, Event, Kind, PullRequest, Release, Source
from ..http import Api, ApiError, marker_html
from ..links import Link
from ..ports import Build
from .plane import header

KIND = "gitea_pr"
PAGE, MAX_PAGES = 50, 100  # a page of Gitea's lists, and how many are read at most
AGENT_ACCOUNTS = {"agent", "agent-runner"}
MENTION = re.compile(r"(?<![\w@])@agent(?![\w-])")
TICKET = re.compile(r"\b[A-Z][A-Z0-9]+-[0-9]+\b")
REVIEWS = {
    "pull_request_review_approved": "approved",
    "pull_request_review_rejected": "request_changes",
    "pull_request_review_comment": "comment",
}


def pr_link(repo: str, number: int) -> Link:
    """A pull request's thread link, as the webhooks and the agent name it."""
    return Link(KIND, f"{repo}#{number}")


# --- What the loop reads of Gitea's payloads (spike 1.4); the rest is ignored ------------------


class User(BaseModel):
    login: str = ""


class Repository(BaseModel):
    full_name: str = ""


class Ref(BaseModel):
    ref: str = ""
    sha: str = ""


class PullRequestBody(BaseModel):
    number: int = 0
    head: Ref = Field(default_factory=Ref)
    base: Ref = Field(default_factory=Ref)
    merged: bool = False


class Issue(BaseModel):
    number: int = 0
    pull_request: dict[str, Any] | None = None


class Text(BaseModel):
    id: int | None = None
    body: str = ""
    content: str = ""  # a review's


class ReleaseBody(BaseModel):
    tag_name: str = ""
    body: str | None = None


class Payload(BaseModel):
    action: str = ""
    number: int | None = None
    sender: User = Field(default_factory=User)
    repository: Repository = Field(default_factory=Repository)
    pull_request: PullRequestBody | None = None
    issue: Issue | None = None
    is_pull: bool = False
    comment: Text = Field(default_factory=Text)
    review: Text = Field(default_factory=Text)
    release: ReleaseBody = Field(default_factory=ReleaseBody)

    @field_validator("sender", "repository", "comment", "review", "release", mode="before")
    @classmethod
    def null_is_empty(cls, value: object) -> object:
        """Gitea sends null for what an event does not have."""
        return {} if value is None else value

    def pr_number(self, event: str | None) -> int | None:
        """The pull request it is about; a comment counts only on a pull request, not an issue."""
        if event == "issue_comment":
            on_pr = self.is_pull or (self.issue is not None and self.issue.pull_request is not None)
            return self.issue.number if on_pr and self.issue else None
        return self.number or (self.pull_request.number if self.pull_request else None)


def payload(body: dict[str, Any]) -> Payload | None:
    try:
        return Payload.model_validate(body)
    except ValidationError:
        return None


def link_of(headers: Mapping[str, str], body: dict[str, Any]) -> Link | None:
    """The pull request an event is about, for routing before anything else is read."""
    parsed = payload(body)
    return link_from(parsed, header(headers, "X-Gitea-Event")) if parsed else None


def link_from(p: Payload, event: str | None) -> Link | None:
    if event == "release" or not p.repository.full_name:
        return None
    number = p.pr_number(event)
    return pr_link(p.repository.full_name, number) if number else None


def to_event(headers: Mapping[str, str], body: dict[str, Any]) -> Event | None:
    """The event, or None when it is nothing for the loop: the agent's own, or without a request."""
    parsed = payload(body)
    if parsed is None or parsed.sender.login in AGENT_ACCOUNTS:
        return None
    kind = header(headers, "X-Gitea-Event-Type") or header(headers, "X-Gitea-Event") or ""
    read = READERS.get(kind)
    if read is None:
        return None
    link = link_from(parsed, header(headers, "X-Gitea-Event"))
    who = Actor(Source.GITEA, parsed.sender.login)
    return read(parsed, kind, link, who, header(headers, "X-Gitea-Delivery") or "")


def pull_request(p: Payload, link: Link) -> PullRequest:
    pr = p.pull_request or PullRequestBody()
    return PullRequest(
        repo=p.repository.full_name,
        number=int(link.ref.rsplit("#", 1)[1]),
        head=pr.head.ref,
        head_sha=pr.head.sha,
        base=pr.base.ref,
    )


def pr_event(p: Payload, kind: str, link: Link | None, who: Actor, delivery: str) -> Event | None:
    """A pull request opened, merged or closed."""
    if link is None:
        return None
    merged = p.pull_request is not None and p.pull_request.merged
    happened = {"opened": Kind.PR_OPENED, "closed": Kind.PR_MERGED if merged else Kind.PR_CLOSED}.get(p.action)
    if happened is None:
        return None
    return Event(Source.GITEA, happened, link, pull_request(p, link), who, delivery_id=delivery)


def comment_event(p: Payload, kind: str, link: Link | None, who: Actor, delivery: str) -> Event | None:
    """A new comment on a pull request that mentions @agent; an edited or deleted one would repeat
    or revive a request."""
    if link is None or p.action != "created" or not MENTION.search(p.comment.body):
        return None
    return Event(
        Source.GITEA,
        Kind.COMMENT,
        link,
        pull_request(p, link),
        who,
        text=p.comment.body,
        mentions_agent=True,
        delivery_id=delivery,
    )


def review_event(p: Payload, kind: str, link: Link | None, who: Actor, delivery: str) -> Event | None:
    """A review: an approval or a request for changes is a signal on its own; a plain review
    comment counts only when it mentions the agent."""
    review, text = REVIEWS[kind], p.review.content
    mentioned = bool(MENTION.search(text))
    if link is None or (review == "comment" and not mentioned):
        return None
    pr = pull_request(p, link)
    return Event(
        Source.GITEA,
        Kind.REVIEW,
        link,
        PullRequest(pr.repo, pr.number, pr.head, pr.head_sha, pr.base, review=review),
        who,
        text=text,
        mentions_agent=mentioned,
        delivery_id=delivery,
    )


def release_event(p: Payload, kind: str, link: Link | None, who: Actor, delivery: str) -> Event | None:
    """A release published, with the tickets its notes name."""
    if p.action != "published":
        return None
    repo, tag = p.repository.full_name, p.release.tag_name
    tickets = tuple(TICKET.findall(p.release.body or ""))
    return Event(
        Source.GITEA,
        Kind.RELEASE,
        Link("gitea_release", f"{repo}@{tag}"),
        Release(repo, tag, tickets),
        who,
        delivery_id=delivery,
    )


# What each kind of webhook (X-Gitea-Event-Type) becomes; a comment is one on a pull request only.
READERS: dict[str, Callable[[Payload, str, Link | None, Actor, str], Event | None]] = {
    "pull_request": pr_event,
    "pull_request_sync": pr_event,
    "pull_request_comment": comment_event,
    "release": release_event,
    **dict.fromkeys(REVIEWS, review_event),
}


def listing(answer: Any) -> list[dict[str, Any]]:
    """A list Gitea's API answered with, as a list of objects."""
    return cast(list[dict[str, Any]], answer or [])


# --- The CodeHost port on Gitea's API, as agent -------------------------------------------------


# The status a build reports first when a commit has several runs: the worst.
BUILD_ORDER = {"failure": 0, "cancelled": 0, "success": 2}


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
            batch = listing(self.api.json("GET", path, params={**params, "limit": PAGE, "page": page}))
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
        comments = listing(self.api.json("GET", f"/repos/{repo}/issues/{number}/comments"))  # all: not paged
        if any(note in (c.get("body") or "") for c in comments):
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

    def build(self, repo: str, commit: str) -> Build | None:
        """The commit's workflow runs, the worst first: a failed one, then one still going, then
        any; a run's status is its conclusion once it is completed."""
        answer: dict[str, Any] = self.api.json("GET", f"/repos/{repo}/actions/runs", params={"head_sha": commit}) or {}
        builds = [
            Build(
                status=str(r.get("conclusion") if r.get("status") == "completed" else r.get("status")),
                url=str(r.get("html_url") or ""),
            )
            for r in listing(answer.get("workflow_runs"))
        ]
        return min(builds, key=lambda b: BUILD_ORDER.get(b.status, 1), default=None)

    def branches_of(self, repo: str, number: int) -> tuple[str, str]:
        pr = self.api.json("GET", f"/repos/{repo}/pulls/{number}")
        return str(pr["head"]["ref"]), str(pr["base"]["ref"])

    def tags(self, repo: str) -> list[str]:
        return [str(tag["name"]) for tag in self.every(f"/repos/{repo}/tags")]

    def unreleased(self, repo: str, release: str, base: str) -> list[str]:
        # "not" is a keyword in Python, hence the dict.
        commits = self.every(
            f"/repos/{repo}/commits",
            **{"sha": base, "not": release, "stat": "false", "verification": "false", "files": "false"},
        )
        return [str(cast(dict[str, Any], c.get("commit") or {}).get("message") or "") for c in commits]

    def describe(self, repo: str, number: int, title: str, body: str) -> None:
        self.api.call("PATCH", f"/repos/{repo}/pulls/{number}", json={"title": title, "body": body})

    def review_comments(self, repo: str, number: int, reviewer: str) -> list[str]:
        reviews = [
            r
            for r in self.every(f"/repos/{repo}/pulls/{number}/reviews")
            if (r.get("user") or {}).get("login") == reviewer
        ]
        if not reviews:
            return []
        latest = max(reviews, key=lambda r: r["id"])
        comments = listing(self.api.json("GET", f"/repos/{repo}/pulls/{number}/reviews/{latest['id']}/comments"))
        return [
            f"{c.get('path')}:{c.get('position') or c.get('original_position') or ''}: {c.get('body')}"
            for c in comments
        ]
