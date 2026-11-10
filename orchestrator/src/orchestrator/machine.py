"""The delivery loop's rules (work-item-orchestration, agent-commands), as tables: a person's
requests by intent and phase (the policy's rules), a run's results by outcome, what follows an
archive, and pull request events. Every handler is small: it changes the step's work item and
acts through the step, which holds the ports; the agent (agent.py) keeps the item.

One run at a time: the item remembers the run going on and takes that run's result only; a
request that needs another run waits until it is done, and a result that arrives after the item
moved on (closed, merged) is dropped. A step is safe to run twice, should it stop halfway: a
comment carries a marker of the delivery and its place in the step and is not written twice, a
pull request is found before it is opened, and a run's name is the same on every try.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .executor import Run
from .links import Links
from .model import (AgentRun, Answer, Command, Intake, Message, Mode, Paused, PrChanged, Request, RunFinished,
                    RunResult, Then, Ticket, Where, WorkItem)
from .policy import ENGINEERS, MEMBERS, OPEN, PEOPLE, Phase, Policy, Rule
from .ports import CodeHost, Tracker
from .projects import Project

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ports:
    """What the agent acts through. executor None: agent runs are off."""

    tracker: Tracker
    code_host: CodeHost
    links: Links
    executor: object | None
    agent_model: str


@dataclass
class Step:
    """One step of a thread: the work item as it becomes, and the agent's hands for it."""

    item: WorkItem
    project: Project
    ports: Ports
    thread_id: str
    delivery: str
    said: int = field(default=0)  # comments so far, for their markers

    @property
    def key(self) -> str:
        return self.item.key

    @property
    def repo(self) -> str:
        return self.project.repo

    def _ticket(self) -> tuple[str, str, str]:
        t = self.item.ticket
        return t.workspace, t.project_id, t.work_item_id

    def set_status(self, phase: Phase) -> None:
        self.ports.tracker.set_status(*self._ticket(), phase)

    def phase(self, phase: Phase) -> None:
        if self.item.phase != phase:
            self.item.phase = phase
            self.set_status(phase)

    def say(self, text: str, where: Where = Where.TICKET) -> None:
        """Says it where asked; on the pull request only once there is one."""
        marker = f"{self.delivery}/{self.said}"
        self.said += 1
        if where == Where.PR and self.item.pr:
            self.ports.code_host.comment(self.repo, self.item.pr, text, marker)
        else:
            self.ports.tracker.comment(*self._ticket(), text, marker)
        self.item.messages.append(Message(author="agent", text=text))

    def start(self, mode: Mode, then: Then = Then.REVIEW, **options) -> None:
        if self.ports.executor is None:
            raise RuntimeError("agent runs are off: no agent image configured")
        self.item.runs += 1
        run = self.item.running = AgentRun(mode=mode, n=self.item.runs, then=then, **options)
        self.ports.executor.start(Run(run.name(self.key), self.thread_id, self.task(run)))

    def task(self, run: AgentRun) -> dict:
        """agent-runner's task.json (agent-runner/entrypoint.sh)."""
        item, project, ticket = self.item, self.project, self.item.ticket
        return {"mode": str(run.mode), "repo": project.repo, "base": project.base,
                "branch": run.branch or ticket.branch, "change": ticket.change, "model": self.ports.agent_model,
                "checks": list(project.checks), "ticket": {"key": ticket.key, "title": ticket.title, "text": ticket.text},
                "answers": [a.model_dump() for a in item.answers],
                **({"feedback": item.feedback} if item.feedback else {}),
                **({"unarchive": True} if run.unarchive else {})}

    def open_pr(self, head: str, title: str, announce: str, own: bool = True) -> None:
        """Finds or opens a pull request into the base branch, links it to the thread and
        announces it in the ticket ("{url}" becomes its address). own: the item's pull request."""
        number = self.ports.code_host.find_or_open_pr(self.repo, head, self.project.base, title,
                                                      f"Ticket {self.key}: {self.item.ticket.title}\n\nOpened by @agent.")
        self.ports.links.link(self.thread_id, self.ports.code_host.pr_link(self.repo, number))
        if own:
            self.item.pr = number
        self.say(announce.format(url=self.ports.code_host.pr_url(self.repo, number)))

    def label(self, name: str) -> None:
        self.ports.code_host.label(self.repo, self.item.pr, name)

    def merge(self) -> None:
        self.ports.code_host.merge(self.repo, self.item.pr)

    def close_pr(self) -> None:
        if self.item.pr:
            self.ports.code_host.close(self.repo, self.item.pr)

    def again(self, run: AgentRun) -> None:
        self.start(run.mode, run.then, branch=run.branch, unarchive=run.unarchive)

    def stop_runs(self) -> None:
        """The item moved on: whatever run goes on, its result no longer counts."""
        self.item.running = None

    def pause(self, run: AgentRun, questions: list[str], text: str) -> None:
        self.item.paused = Paused(phase=self.item.phase, run=run, questions=questions)
        self.phase(Phase.NEEDS_INFO)
        self.say(text)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "change"


# --- a ticket assigned to @agent -----------------------------------------------------------------

STARTABLE = {None, Phase.CLOSED}  # assigned again while the work goes on: nothing to do


def intake(s: Step, c: Intake) -> None:
    """A new spec to draft on the ticket's own branch. A thread closed before keeps its run count
    and its conversation."""
    if s.item.phase not in STARTABLE:
        return
    key, name = f"{s.project.identifier}-{c.sequence_id}", slug(c.title)
    old = s.item
    s.item = WorkItem(runs=old.runs, messages=old.messages, applied=old.applied, ticket=Ticket(
        project=s.project.name, key=key, title=c.title, text=c.description, workspace=c.workspace,
        project_id=c.project_id, work_item_id=c.work_item_id,
        branch=f"feat/{key}_{name}", change=f"{key.lower()}-{name}"))
    s.phase(Phase.DRAFTING)
    s.say(f"Drafting a spec for {key}. I will link the pull request here when it is ready.")
    s.start(Mode.PROPOSE)


# --- a person's requests (agent-commands) --------------------------------------------------------

def approve_spec(s: Step, r: Request) -> None:
    s.phase(Phase.IMPLEMENTING)
    s.say("Spec approved. Implementing it in the same pull request.", r.via)
    s.start(Mode.APPLY)


def finish_code_review(then: Then, announcement: str) -> Callable[[Step, Request], None]:
    """Approving the code archives the change; the archive commit comes last before the merge
    (design decision 7). An archived change goes on to what follows right away."""

    def handler(s: Step, r: Request) -> None:
        if s.item.archived:
            AFTER_ARCHIVE[then](s, r.via)
            return
        s.say(announcement, r.via)
        s.start(Mode.ARCHIVE, then)

    return handler


def feedback_of(r: Request) -> str:
    return "\n".join([r.text, *r.feedback]).strip()


def revise_spec(s: Step, r: Request) -> None:
    s.item.feedback = feedback_of(r)
    s.phase(Phase.DRAFTING)
    s.say("Revising the spec.", r.via)
    s.start(Mode.PROPOSE)


def fix_code(s: Step, r: Request) -> None:
    """An archived change goes back to openspec/changes/ in the fix run, so the next approval
    archives it again as the last commit."""
    unarchive, s.item.archived = s.item.archived, False
    s.item.feedback, s.item.fix_reply = feedback_of(r), r.via
    s.phase(Phase.IMPLEMENTING)
    s.say("Implementing a fix.", r.via)
    s.start(Mode.FIX, unarchive=unarchive)


def reject(s: Step, r: Request) -> None:
    s.stop_runs()
    s.phase(Phase.CLOSED)
    s.close_pr()
    s.say(f"Closed {s.key}; no further work on it.", r.via)


def resume(s: Step, r: Request) -> None:
    """The answers to the agent's questions, or, after a failed run, whatever the person adds;
    then the same run again, in the phase the item was in."""
    paused, s.item.paused = s.item.paused, None
    s.item.answers += [Answer(question=q, answer=r.text) for q in paused.questions] or [Answer(answer=r.text)]
    s.phase(paused.phase)
    s.say("Thanks, going on.", r.via)
    s.again(paused.run)


def archive_in_own_pr(s: Step, r: Request) -> None:
    """A person merged before the archive: the archive gets a branch and a pull request of its own."""
    if s.item.archived:
        s.say(f"The change of {s.key} is archived already.", r.via)
        return
    s.say("Archiving the change in a pull request of its own.", r.via)
    s.start(Mode.ARCHIVE, Then.ARCHIVE_PR, branch=f"archive/{s.key}")


def release_later(s: Step, r: Request) -> None:
    s.say("Releases come with the next part of the loop.", r.via)


POLICY = Policy([
    Rule("approve", frozenset({Phase.SPEC_REVIEW}), PEOPLE, approve_spec),
    Rule("approve", frozenset({Phase.CODE_REVIEW}), ENGINEERS,
         finish_code_review(Then.REVIEW, "Archiving the change into the specs.")),
    Rule("merge", frozenset({Phase.CODE_REVIEW}), ENGINEERS,
         finish_code_review(Then.MERGE, "Archiving the change into the specs, then merging.")),
    Rule("request_changes", frozenset({Phase.SPEC_REVIEW}), PEOPLE, revise_spec),
    Rule("request_changes", frozenset({Phase.CODE_REVIEW}), PEOPLE, fix_code),
    Rule("reject", OPEN, PEOPLE, reject, while_running=True),
    Rule("answer", frozenset({Phase.NEEDS_INFO}), MEMBERS, resume),
    Rule("archive", frozenset({Phase.ON_DEV}), ENGINEERS, archive_in_own_pr),
    Rule("prepare_release", None, PEOPLE, release_later, while_running=True),
])


def request(s: Step, r: Request) -> None:
    """A request the policy allows goes to its rule's handler; any other gets the reason. A status
    a person set in the tracker that left the phase as it was goes back to the phase's own."""
    s.item.messages.append(Message(author=r.author, text=r.text))
    before = s.item.phase
    decision = POLICY.decide(r.intent, s.item.phase, r.roles, s.project.name, s.key)
    if not decision.allowed:
        s.say(decision.reason, r.via)
    elif s.item.running and not decision.rule.while_running:
        s.say(f"I am still on the {s.item.running.mode} run of {s.key}. Ask again when it is done.", r.via)
    else:
        decision.rule.handler(s, r)
    if r.status_signal and before and s.item.phase == before:
        s.set_status(before)


# --- a run's result (design decision 7) ----------------------------------------------------------

def spec_ready(s: Step, run: AgentRun, result: RunResult) -> None:
    s.item.feedback = ""
    s.open_pr(s.item.ticket.branch, f"{s.key}: {s.item.ticket.title}",
              "The spec is ready for review: {url}. Approve it with @agent, or say what to change.")
    s.phase(Phase.SPEC_REVIEW)


READY = {Mode.APPLY: "The code is ready for review", Mode.FIX: "The fix is ready"}


def code_ready(s: Step, run: AgentRun, result: RunResult) -> None:
    """The code goes to review; with previews on, through a preview deployment first."""
    s.item.feedback = ""
    if s.project.previews:
        s.label("preview")
    s.phase(Phase.DEPLOYING if s.project.previews else Phase.CODE_REVIEW)
    where = s.item.fix_reply if run.mode == Mode.FIX else Where.PR
    s.say(READY.get(run.mode, READY[Mode.APPLY]) + ". Approve it, or ask @agent to merge it into dev.", where)


def archived(s: Step, run: AgentRun, result: RunResult) -> None:
    s.item.archived = True
    AFTER_ARCHIVE[run.then](s, Where.PR)


def questions(s: Step, run: AgentRun, result: RunResult) -> None:
    lines = "\n".join(f"{n}. {q}" for n, q in enumerate(result.questions, 1))
    s.pause(run, result.questions, f"I need a few answers before I go on. Reply with @agent:\n{lines}")


def failed(s: Step, run: AgentRun, result: RunResult) -> None:
    """A failed run, or a result the loop does not know: a person decides whether to retry."""
    s.pause(run, [], f"The {run.mode} run failed: {result.reason or 'no reason given'}. "
                     "Reply with @agent to run it again; anything you add goes to the agent.")


OUTCOMES = {"spec_ready": spec_ready, "code_ready": code_ready, "archived": archived, "questions": questions}


def run_finished(s: Step, c: RunFinished) -> None:
    if not s.item.is_running(c.job):
        log.info("result of run %s dropped: the item waits for %s", c.job,
                 s.item.running.name(s.key) if s.item.running else "no run")
        return
    run, s.item.running = s.item.running, None
    OUTCOMES.get(c.result.result, failed)(s, run, c.result)


# --- what follows an archive ---------------------------------------------------------------------

def ready_to_merge(s: Step, where: Where) -> None:
    s.say("The change is archived into the specs; the pull request is ready to merge.", where)


def merge_now(s: Step, where: Where) -> None:
    s.merge()
    s.phase(Phase.ON_DEV)
    s.say(f"{s.key} is merged into {s.project.base}.")


def open_archive_pr(s: Step, where: Where) -> None:
    s.open_pr(f"archive/{s.key}", f"{s.key}: archive the change", "The archive pull request is open: {url}.", own=False)


AFTER_ARCHIVE = {Then.REVIEW: ready_to_merge, Then.MERGE: merge_now, Then.ARCHIVE_PR: open_archive_pr}


# --- a person merged or closed the pull request --------------------------------------------------

def pr_merged(s: Step) -> None:
    s.phase(Phase.ON_DEV)
    if not s.item.archived:
        s.say(f"The specs in pull request #{s.item.pr} on {s.key} were not archived. "
              "Reply \"@agent ok, create archive PR\" to have them archived.")


def pr_closed(s: Step) -> None:
    s.phase(Phase.CLOSED)
    s.say(f"The pull request was closed, so {s.key} is closed too.")


PR_EVENTS = {True: pr_merged, False: pr_closed}


def pr_changed(s: Step, c: PrChanged) -> None:
    """Only an open item moves; its run, if any, no longer counts."""
    if s.item.phase in OPEN:
        s.stop_runs()
        PR_EVENTS[c.merged](s)


# --- a step --------------------------------------------------------------------------------------

STEPS: dict[type, Callable] = {Intake: intake, Request: request, RunFinished: run_finished, PrChanged: pr_changed}


def advance(item: WorkItem, command: Command, project: Project, ports: Ports, thread_id: str) -> WorkItem:
    """Takes the step a command makes and returns the new work item; the one passed in stays as
    it was, so a step that fails halfway leaves the thread where it was."""
    step = Step(item.model_copy(deep=True), project, ports, thread_id, command.delivery)
    STEPS[type(command)](step, command)
    return step.item
