"""Who may ask for what, in which phase (design decision 5). Code decides, never the model: a
misread comment costs a question or a refusal, never an action nobody allowed.

The policy is a table of rules, each naming an intent, the phases it applies in, the roles that
may ask for it and what to do then. The loop (machine.py) owns the table; the policy only finds
the rule that allows a request, or says why none does.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    """A work item's phase; the tracker shows it as a status of the same name."""

    DRAFTING = "Drafting"
    NEEDS_INFO = "Needs info"
    SPEC_REVIEW = "Spec review"
    IMPLEMENTING = "Implementing"
    DEPLOYING = "Deploying to QA"
    CODE_REVIEW = "Code review"
    ON_DEV = "On dev"
    RELEASED = "Released"
    CLOSED = "Closed"


# Until the merge; after it, there is nothing left to reject.
OPEN = frozenset({Phase.DRAFTING, Phase.NEEDS_INFO, Phase.SPEC_REVIEW, Phase.IMPLEMENTING, Phase.DEPLOYING,
                  Phase.CODE_REVIEW})
PEOPLE = frozenset({"product_owner", "engineer"})
ENGINEERS = frozenset({"engineer"})
MEMBERS = None  # any person of the project

# What the model may read a comment as.
INTENTS = ("approve", "request_changes", "reject", "answer", "merge", "archive", "prepare_release", "none")

ROLE_WORDS = {PEOPLE: "a product owner or an engineer", ENGINEERS: "an engineer"}
UNSURE = ("I am not sure what you are asking. Say, for example, \"@agent looks good\", "
          "\"@agent please change ...\" or \"@agent merge it into dev\".")


@dataclass(frozen=True)
class Rule:
    intent: str
    verb: str  # what a refusal says cannot be done: "... so I cannot <verb> now."
    phases: frozenset[Phase] | None  # None: any phase, the request is about the project
    roles: frozenset[str] | None  # None: any person of the project
    handler: Any  # what the loop does when the rule allows a request
    while_running: bool = False  # may be asked while an agent run goes on

    def covers(self, phase: Phase | None) -> bool:
        return self.phases is None or phase in self.phases

    def admits(self, roles: frozenset[str]) -> bool:
        return self.roles is None or bool(roles & self.roles)


@dataclass(frozen=True)
class Decision:
    rule: Rule | None = None
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.rule is not None


class Policy:
    def __init__(self, rules: Iterable[Rule]):
        self.rules: dict[str, list[Rule]] = defaultdict(list)
        for rule in rules:
            self.rules[rule.intent].append(rule)

    def decide(self, intent: str, phase: Phase | None, roles: frozenset[str] | None, project: str,
               key: str = "") -> Decision:
        """The rule that lets a person with these roles (None: not a person of the project) ask
        for this now, or the reason nothing does."""
        if roles is None:
            return Decision(reason=f"I only take requests from the people of {project}.")
        if intent not in self.rules:
            return Decision(reason=UNSURE)
        in_phase = [rule for rule in self.rules[intent] if rule.covers(phase)]
        if not in_phase:
            where = f"{key} is in {phase}" if phase else "this item is not in the loop"
            return Decision(reason=f"{where}, so I cannot {self.rules[intent][0].verb} now.")
        rule = next((rule for rule in in_phase if rule.admits(roles)), None)
        if rule is None:
            needed = ROLE_WORDS.get(in_phase[0].roles, "someone else")
            return Decision(reason=f"Only {needed} of {project} can {in_phase[0].verb} in {phase}.")
        return Decision(rule)


# Explicit signals need no model (design decision 5): an event kind, the field it is read from,
# and what its values stand for.
SIGNALS = {
    "status": ("state", {"Approved": "approve", "Closed": "reject"}),
    "review": ("review", {"approved": "approve", "request_changes": "request_changes"}),
}


def signal(kind: str, data: dict) -> str | None:
    """The intent an explicit signal stands for: a status a person set, or a review's verdict."""
    field, meanings = SIGNALS.get(kind, ("", {}))
    return meanings.get(data.get(field, ""))
