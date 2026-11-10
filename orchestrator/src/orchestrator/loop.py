"""The delivery loop's rules (work-item-orchestration, agent-commands): what an event does to a
work item, given its thread's state. decide() is pure: it returns the new state and the actions
to take, and act() takes them through the ports. The graph (graph.py) runs the two in a
LangGraph thread, which keeps the state.

A step is safe to run twice. Every action carries a marker made of the delivery and its place in
the step, a comment with that marker is not written again, a run's name follows the thread's run
counter, and the thread keeps the deliveries it applied.

One run at a time: the thread remembers the run going on, takes the result of that run only, and
asks people to wait with a request that needs another one. A run's result that arrives after the
item moved on (closed, merged) is dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .policy import OPEN, Phase, decide as allowed

APPLIED_KEPT = 200


@dataclass(frozen=True)
class Action:
    kind: str  # status | ticket | pr | reply | run | open_pr | merge | close_pr | label
    text: str = ""
    mode: str = ""  # for run: propose | apply | fix | archive
    extra: dict = field(default_factory=dict)


@dataclass
class Step:
    state: dict
    actions: list[Action] = field(default_factory=list)

    def do(self, kind: str, text: str = "", mode: str = "", **extra) -> None:
        self.actions.append(Action(kind, text, mode, extra))

    def phase(self, phase: Phase) -> None:
        if self.state.get("phase") != phase:
            self.state["phase"] = str(phase)
            self.do("status", str(phase))

    def run(self, mode: str, **task) -> None:
        self.state["runs"] = self.state.get("runs", 0) + 1
        self.state["run_mode"] = mode
        self.state["run_task"] = task  # what a retry of this run needs again: its branch, unarchive
        self.state["running"] = run_name(self.state["key"], mode, self.state["runs"])
        self.do("run", mode=mode, **task)

    def stop_runs(self) -> None:
        """The item moved on: whatever run is going, its result no longer counts."""
        self.state["running"] = ""

    def say(self, who: str, text: str) -> None:
        self.state.setdefault("messages", []).append({"from": who, "text": text})


def run_name(key: str, mode: str, n: int) -> str:
    """A run's Job and ConfigMap name: the same on every try of a step."""
    return f"{key.lower()}-{mode}-{n}"


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "change"


def intake(state: dict, event: dict, project: dict) -> Step:
    """A ticket assigned to @agent: a new thread, a spec to draft on its own branch. A thread
    closed before keeps counting its runs, so the new runs' names are new."""
    data = event["data"]
    key = f"{project['identifier']}-{data['sequence_id']}"
    name = slug(data.get("title", ""))
    step = Step({**state, "project": project["name"], "key": key, "title": data.get("title", ""),
                 "text": data.get("description", ""), "workspace": data.get("workspace"),
                 "project_id": data.get("project_id"), "work_item_id": data.get("work_item_id"),
                 "base": project.get("base", "development"),
                 "branch": f"feat/{key}_{name}", "change": f"{key.lower()}-{name}", "pr": None,
                 "answers": [], "feedback": "", "archived": False, "runs": state.get("runs", 0),
                 "merge_after_archive": False, "running": ""})
    step.phase(Phase.DRAFTING)
    step.do("ticket", f"Drafting a spec for {key}. I will link the pull request here when it is ready.")
    step.run("propose")
    return step


STARTS_A_RUN = {"approve", "merge", "request_changes", "answer", "archive"}


def on_request(state: dict, intent: str, text: str, roles, source: str, extra_feedback: list[str],
               status_signal: bool = False) -> Step:
    """A person's request, read from a comment or an explicit signal, checked by the policy. A
    status a person set in the tracker and the agent refused is set back to the phase."""
    step = Step(dict(state))
    phase = Phase(state["phase"]) if state.get("phase") else None

    def refuse(reason: str) -> Step:
        if status_signal and phase:
            step.do("status", str(phase))
        step.do("reply", reason)
        return step

    decision = allowed(intent, phase, roles, state.get("project", ""), state.get("key", ""), state.get("archived", False))
    if not decision.allowed:
        return refuse(decision.reason)
    if state.get("running") and intent in STARTS_A_RUN:
        return refuse(f"I am still on the {state.get('run_mode')} run of {state.get('key')}. "
                      "Ask again when it is done.")

    if intent == "approve" and phase == Phase.SPEC_REVIEW:
        step.phase(Phase.IMPLEMENTING)
        step.do("reply", "Spec approved. Implementing it in the same pull request.")
        step.run("apply")
    elif intent in ("approve", "merge") and phase == Phase.CODE_REVIEW:
        # The archive commit comes last before the merge (design decision 7). A request to merge
        # counts as an approval: archive first, then merge.
        step.state["merge_after_archive"] = intent == "merge"
        if state.get("archived"):
            if intent == "merge":
                step.do("merge")
                merged(step)
            else:
                step.do("reply", "The change is archived; the pull request is ready to merge.")
        else:
            step.do("reply", "Archiving the change into the specs" + (", then merging." if intent == "merge" else "."))
            step.run("archive")
    elif intent == "request_changes" and phase == Phase.SPEC_REVIEW:
        step.state["feedback"] = "\n".join([text, *extra_feedback]).strip()
        step.phase(Phase.DRAFTING)
        step.do("reply", "Revising the spec.")
        step.run("propose")
    elif intent == "request_changes" and phase == Phase.CODE_REVIEW:
        # An archived change goes back to openspec/changes/ first, so that the archive commit is
        # again the last one before the merge, after the fix is approved.
        step.state["feedback"] = "\n".join([text, *extra_feedback]).strip()
        step.state["fix_source"] = source
        step.state["merge_after_archive"] = False
        unarchive = bool(state.get("archived"))
        step.state["archived"] = False
        step.phase(Phase.IMPLEMENTING)
        step.do("reply", "Implementing a fix.")
        step.run("fix", **({"unarchive": True} if unarchive else {}))
    elif intent == "reject":
        step.stop_runs()
        step.phase(Phase.CLOSED)
        if state.get("pr"):
            step.do("close_pr")
        step.do("reply", f"Closed {state.get('key')}; no further work on it.")
    elif intent == "answer" and phase == Phase.NEEDS_INFO:
        # The answers to the agent's questions, or, after a failed run, whatever the person adds.
        questions = state.get("questions", [])
        step.state["answers"] = [*state.get("answers", []),
                                 *({"question": q, "answer": text} for q in questions)] if questions else \
            [*state.get("answers", []), {"question": "", "answer": text}]
        step.state["questions"] = []
        back = Phase(state.get("return_phase") or Phase.DRAFTING)
        step.phase(back)
        step.do("reply", "Thanks, going on.")
        step.run(state.get("run_mode") or "propose", **(state.get("run_task") or {}))
    elif intent == "archive" and phase == Phase.ON_DEV:
        step.state["archive_pr"] = True
        step.do("reply", "Archiving the change in a pull request of its own.")
        step.run("archive", branch=f"archive/{state['key']}")
    elif intent == "prepare_release":
        step.do("reply", "Releases come with the next part of the loop.")
    return step


def on_run(state: dict, job: str, result: dict, previews: bool) -> Step:
    """An agent run finished (design decision 7). Only the result of the run going on counts."""
    step = Step(dict(state))
    if not current(state, job):
        return step
    step.state["running"] = ""
    outcome = result.get("result")
    key = state.get("key", "")
    if outcome == "questions":
        step.state["questions"] = result.get("questions", [])
        step.state["return_phase"] = state.get("phase")
        step.phase(Phase.NEEDS_INFO)
        lines = "\n".join(f"{n}. {q}" for n, q in enumerate(step.state["questions"], 1))
        step.do("ticket", f"I need a few answers before I go on. Reply with @agent:\n{lines}")
    elif outcome == "spec_ready":
        step.state["feedback"] = ""
        step.do("open_pr")
        step.phase(Phase.SPEC_REVIEW)
        step.do("ticket", "The spec is ready for review: {pr_url}. Approve it with @agent, or say what to change.")
    elif outcome == "code_ready":
        step.state["feedback"] = ""
        fixed = state.get("run_mode") == "fix"
        if previews:
            step.do("label", "preview")
            step.phase(Phase.DEPLOYING)
        else:
            step.phase(Phase.CODE_REVIEW)
        where = "pr" if state.get("fix_source", "gitea") == "gitea" else "ticket"
        step.do(where if fixed else "pr", ("The fix is ready" if fixed else "The code is ready for review")
                + ". Approve it, or ask @agent to merge it into dev.")
    elif outcome == "archived":
        step.state["archived"] = True
        if state.get("archive_pr"):
            step.do("open_pr", base=state.get("base", "development"), head=f"archive/{key}", title=f"{key}: archive the change")
            step.do("ticket", "The archive pull request is open: {pr_url}.")
        elif state.get("merge_after_archive"):
            step.do("merge")
            merged(step)
        else:
            step.do("pr", "The change is archived into the specs. Ready to merge.")
    else:  # failed, or a result the loop does not know: a person decides whether to retry
        step.state["questions"] = []
        step.state["return_phase"] = state.get("phase")
        step.phase(Phase.NEEDS_INFO)
        step.do("ticket", f"The {state.get('run_mode', 'agent')} run failed: {result.get('reason', 'no reason given')}. "
                          "Reply with @agent to run it again; anything you add goes to the agent.")
    return step


def current(state: dict, job: str) -> bool:
    """Whether a run's result is that of the run the thread waits for."""
    return bool(job) and job == state.get("running")


def merged(step: Step) -> None:
    step.phase(Phase.ON_DEV)
    step.do("ticket", f"{step.state.get('key')} is merged into {step.state.get('base', 'development')}.")


def on_pr(state: dict, kind: str) -> Step:
    """A person merged or closed the pull request in the git server."""
    step = Step(dict(state))
    if kind == "pr_merged" and state.get("phase") not in (Phase.ON_DEV, Phase.RELEASED, Phase.CLOSED):
        step.stop_runs()
        step.phase(Phase.ON_DEV)
        if not state.get("archived"):
            step.do("ticket", f"The specs in pull request #{state.get('pr')} on {state.get('key')} were not archived. "
                              "Reply \"@agent ok, create archive PR\" to have them archived.")
    elif kind == "pr_closed" and Phase(state.get("phase") or Phase.CLOSED) in OPEN:
        step.stop_runs()
        step.phase(Phase.CLOSED)
        step.do("ticket", f"The pull request was closed, so {state.get('key')} is closed too.")
    return step


def applied(state: dict, delivery: str) -> bool:
    return delivery in (state.get("applied") or [])


def remember(state: dict, delivery: str) -> dict:
    return {**state, "applied": [*(state.get("applied") or [])[-(APPLIED_KEPT - 1):], delivery]}
