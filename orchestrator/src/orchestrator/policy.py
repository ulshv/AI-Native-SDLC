"""Who may ask for what, in which phase (design decision 5). Code decides, never the model: a
misread comment costs a question or a refusal, never an action nobody allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
OPEN = {Phase.DRAFTING, Phase.NEEDS_INFO, Phase.SPEC_REVIEW, Phase.IMPLEMENTING, Phase.DEPLOYING, Phase.CODE_REVIEW}
PEOPLE = frozenset({"product_owner", "engineer"})
ENGINEERS = frozenset({"engineer"})
MEMBERS = None  # any person of the project

INTENTS = ("approve", "request_changes", "reject", "answer", "merge", "archive", "prepare_release", "none")

# intent -> [(phases, roles)]; None phases: any (the request is about the project, not the item).
RULES: dict[str, list[tuple[set[Phase] | None, frozenset[str] | None]]] = {
    "approve": [({Phase.SPEC_REVIEW}, PEOPLE), ({Phase.CODE_REVIEW}, ENGINEERS)],
    "request_changes": [({Phase.SPEC_REVIEW, Phase.CODE_REVIEW}, PEOPLE)],
    "reject": [(OPEN, PEOPLE)],
    "answer": [({Phase.NEEDS_INFO}, MEMBERS)],
    "merge": [({Phase.CODE_REVIEW}, ENGINEERS)],
    "archive": [({Phase.ON_DEV}, ENGINEERS)],
    "prepare_release": [(None, PEOPLE)],
}

ROLE_WORDS = {PEOPLE: "a product owner or an engineer", ENGINEERS: "an engineer"}
VERBS = {"approve": "approve", "request_changes": "request changes", "reject": "reject", "answer": "answer",
         "merge": "merge", "archive": "archive the change", "prepare_release": "prepare a release"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def decide(intent: str, phase: Phase | None, roles: frozenset[str] | None, project: str, key: str = "",
           archived: bool = False) -> Decision:
    """Whether a person with these roles (None: not a person of the project) may do this now."""
    if roles is None:
        return Decision(False, f"I only take requests from the people of {project}.")
    rules = RULES.get(intent)
    if not rules:
        return Decision(False, "I did not understand what to do.")
    in_phase = [(phases, allowed) for phases, allowed in rules if phases is None or phase in phases]
    if not in_phase:
        where = f"{key} is in {phase}" if phase else "this item is not in the loop"
        return Decision(False, f"{where}, so I cannot {VERBS[intent]} it now.")
    for _, allowed in in_phase:
        if allowed is None or roles & allowed:
            if intent == "archive" and archived:
                return Decision(False, f"The change of {key} is archived already.")
            return Decision(True)
    needed = in_phase[0][1]
    return Decision(False, f"Only {ROLE_WORDS.get(needed, 'someone else')} of {project} can {VERBS[intent]} in {phase}.")


# Explicit signals need no model (design decision 5).
STATUS_SIGNALS = {"Approved": "approve", "Closed": "reject"}
REVIEW_SIGNALS = {"approved": "approve", "request_changes": "request_changes"}


def signal(kind: str, data: dict) -> str | None:
    """The intent an explicit signal stands for: a status a person set, or a review's verdict."""
    if kind == "status":
        return STATUS_SIGNALS.get(data.get("state", ""))
    if kind == "review":
        return REVIEW_SIGNALS.get(data.get("review", ""))
    return None
