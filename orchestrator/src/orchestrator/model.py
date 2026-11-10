"""The agent's data: a work item as its thread keeps it (the ticket, the pull request, the run
going on, the conversation, the deliveries applied) and the commands a thread's step is about.
Both live in the LangGraph checkpoint as JSON. Fields added later need defaults, so the threads
already saved still load.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .policy import Intent, Phase

APPLIED_KEPT = 200


class Mode(StrEnum):
    PROPOSE = "propose"
    APPLY = "apply"
    FIX = "fix"
    ARCHIVE = "archive"


class Then(StrEnum):
    """What follows an archive run (design decision 7)."""

    REVIEW = "review"  # people merge when they like
    MERGE = "merge"  # the approval was a request to merge: merge right away
    ARCHIVE_PR = "archive_pr"  # a person merged first: the archive goes into a pull request of its own


class Where(StrEnum):
    TICKET = "ticket"
    PR = "pr"


def run_name(key: str, mode: str, n: int) -> str:
    """A run's Job and ConfigMap name: the same on every try of a step."""
    return f"{key.lower()}-{mode}-{n}"


class AgentRun(BaseModel):
    mode: Mode
    n: int
    then: Then = Then.REVIEW
    branch: str = ""  # a branch other than the item's, for an archive pull request
    unarchive: bool = False  # a fix after the archive: take the archive commit back first

    def name(self, key: str) -> str:
        return run_name(key, self.mode, self.n)


class Ticket(BaseModel):
    project: str
    key: str
    title: str = ""
    text: str = ""
    workspace: str = ""
    project_id: str = ""
    work_item_id: str = ""
    branch: str
    change: str


class Paused(BaseModel):
    """Needs info: the agent asked, or a run failed, and waits for a person."""

    phase: Phase
    run: AgentRun  # run again on the answer
    questions: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    question: str = ""
    answer: str


class Message(BaseModel):
    author: str
    text: str


class WorkItem(BaseModel):
    phase: Phase | None = None
    ticket: Ticket | None = None
    pr: int | None = None
    archived: bool = False
    runs: int = 0  # every run of the thread, so a run's name is never used twice
    running: AgentRun | None = None
    paused: Paused | None = None
    answers: list[Answer] = Field(default_factory=list)
    feedback: str = ""
    fix_reply: Where = Where.PR  # where the result of a fix is announced: where it was asked for
    messages: list[Message] = Field(default_factory=list)
    applied: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return self.ticket.key if self.ticket else ""

    @property
    def project(self) -> str:
        return self.ticket.project if self.ticket else ""

    def last_said(self) -> str:
        return next((m.text for m in reversed(self.messages) if m.author == "agent"), "")

    def applied_before(self, delivery: str) -> bool:
        return delivery in self.applied

    def remember(self, delivery: str) -> None:
        self.applied = [*self.applied[-(APPLIED_KEPT - 1) :], delivery]

    def summary(self) -> dict[str, Any]:
        """What the thread API shows; the rest is the loop's bookkeeping."""
        ticket = self.ticket
        return {
            "phase": self.phase,
            "key": self.key,
            "project": self.project,
            "title": ticket.title if ticket else "",
            "branch": ticket.branch if ticket else "",
            "change": ticket.change if ticket else "",
            "pr": self.pr,
            "archived": self.archived,
            "questions": self.paused.questions if self.paused else [],
        }


# --- commands: what a step is about -----------------------------------------------------------


class CommandBase(BaseModel):
    delivery: str  # source/id: a thread applies each delivery once
    project: str = ""  # the target project's name, when the item does not know it yet


class Intake(CommandBase):
    """A ticket assigned to @agent."""

    kind: Literal["intake"] = "intake"
    sequence_id: int
    title: str = ""
    description: str = ""
    workspace: str = ""
    project_id: str = ""
    work_item_id: str = ""


class Request(CommandBase):
    """A person's request: an explicit signal or a comment to @agent, read as an intent."""

    kind: Literal["request"] = "request"
    intent: Intent
    text: str = ""
    author: str = "someone"
    roles: frozenset[str] | None = None  # None: not a person of the project
    via: Where = Where.TICKET  # where it was asked, and where the answer goes
    pr: int | None = None  # the pull request it was asked on, when via is a pull request
    feedback: list[str] = Field(default_factory=list)  # a review's inline comments
    status_signal: bool = False  # a status a person set in the tracker


class RunResult(BaseModel):
    """agent-runner's termination message."""

    model_config = ConfigDict(extra="allow")
    result: str = "failed"
    reason: str = ""
    questions: list[str] = Field(default_factory=list)


class RunFinished(CommandBase):
    kind: Literal["run"] = "run"
    job: str
    result: RunResult = Field(default_factory=RunResult)


class PrChanged(CommandBase):
    """A person merged or closed the item's pull request in the git server."""

    kind: Literal["pr"] = "pr"
    merged: bool


Command = Annotated[Intake | Request | RunFinished | PrChanged, Field(discriminator="kind")]
COMMANDS: TypeAdapter[Command] = TypeAdapter(Command)
