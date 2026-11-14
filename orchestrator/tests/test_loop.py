"""The loop end to end in the orchestrator (task 7.8): webhooks from the fixtures, the real queue,
thread links and LangGraph checkpoints in Postgres; Plane, Gitea, the model and agent runs faked.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FIXTURES

from orchestrator import agent, intents
from orchestrator.agent import Agent
from orchestrator.links import Link, Links
from orchestrator.ports import Build
from orchestrator.previews import event
from orchestrator.projects import Projects
from orchestrator.queue import Delivery, PostgresQueue

REPO_PROJECTS = Path(__file__).parents[2] / "platform" / "projects"
AGENT = "acf51e1d-b7bc-4e95-a61d-9deba97c5a30"
PERSON = "525f2c23-8de0-439d-b238-577d889c9918"  # the fixtures' person in Plane
ITEM = "plane_work_item:72de36e4-09bb-4182-9176-097a5a93da7a/d71d47ba-8b66-45ce-bd4d-8cffb7a1c48a"


class FakeTracker:
    def __init__(self):
        self.statuses, self.comments = [], []

    def agent_id(self):
        return AGENT

    def username(self, user_id):
        return {PERSON: "sergey"}.get(user_id, "")

    def identifier(self, workspace, project_id):
        return "EWA" if project_id == "72de36e4-09bb-4182-9176-097a5a93da7a" else None

    def set_status(self, workspace, project_id, item_id, status):
        self.statuses.append(status)

    def comment(self, workspace, project_id, item_id, text, marker):
        if marker not in [m for m, _ in self.comments]:
            self.comments.append((marker, text))


class FakeCodeHost:
    def __init__(self):
        self.prs, self.comments, self.merged, self.closed, self.labels = {}, [], [], [], []
        self.builds = {}

    def find_or_open_pr(self, repo, head, base, title, body):
        for n, pr in self.prs.items():
            if pr == (head, base):
                return n
        n = 3 + len(self.prs)  # the fixtures' pull request is #3
        self.prs[n] = (head, base)
        return n

    def pr_link(self, repo, number):
        return Link("gitea_pr", f"{repo}#{number}")

    def pr_url(self, repo, number):
        return f"https://gitea.sdlc.internal/{repo}/pulls/{number}"

    def comment(self, repo, number, text, marker):
        if marker not in [m for m, _, _ in self.comments]:
            self.comments.append((marker, number, text))

    def label(self, repo, number, label):
        self.labels.append((number, label))

    def merge(self, repo, number):
        if number not in self.merged:
            self.merged.append(number)

    def close(self, repo, number):
        self.closed.append(number)

    def review_comments(self, repo, number, reviewer):
        return ["src/orders.ts:4: keep the filter after reload"]

    def build(self, repo, commit):
        return self.builds.get(commit)


class FakeExecutor:
    def __init__(self):
        self.runs, self.cancelled = {}, []

    def cancel(self, name):
        self.cancelled.append(name)

    def start(self, run):
        self.runs.setdefault(run.name, run)
        return "started"


class FakePreviews:
    """The threads' waits for their previews; the test says when a preview serves its commit."""

    def __init__(self):
        self.waits, self.forgotten = {}, []

    def watch(self, thread_id, url, commit):
        self.waits[thread_id] = (url, commit)

    def forget(self, thread_id):
        self.waits.pop(thread_id, None)
        self.forgotten.append(thread_id)


class FakeChat:
    """Reads the intent from a table of comments, as a model would."""

    def __init__(self):
        self.answers, self.calls = {}, 0

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, prompt):
        self.calls += 1
        comment = prompt.rsplit("The comment:\n", 1)[1]
        name, confidence = self.answers.get(comment, ("none", 1.0))
        return intents.ModelReading(intent=name, confidence=confidence)


class FakeReply:
    """Writes the agent's answers, as the model would, and keeps the facts it was given."""

    def __init__(self):
        self.facts = []

    def __call__(self, facts):
        self.facts.append(facts)
        return f"{'unclear' if facts.unclear else 'answer'}: {facts.key} is in {facts.phase}"


@pytest.fixture
def world(pool, database_url):
    with agent.checkpointer(database_url, 4) as saver:
        svc = SimpleNamespace(
            projects=Projects.load(REPO_PROJECTS),
            links=Links(pool),
            queue=PostgresQueue(pool),
            tracker=FakeTracker(),
            code_host=FakeCodeHost(),
            executor=FakeExecutor(),
            previews=FakePreviews(),
            chat=FakeChat(),
            reply=FakeReply(),
            agent_model="llm/deepseek/deepseek-v4-flash-0731",
            threshold=0.7,
        )

        class World:
            def __init__(self):
                self.svc, self.queue, self.n = svc, svc.queue, 0
                self.fresh()

            def fresh(self):
                """A new orchestrator process on the same database."""
                self.dispatch = Agent(checkpointer=saver, **vars(self.svc))

            def hook(self, source, name, *, delivery=None, **changes):
                d = json.loads((FIXTURES / source / f"{name}.json").read_text())
                for path, value in changes.items():
                    target = d["body"]
                    *parents, last = path.split("__")
                    for p in parents:
                        target = target[p]
                    target[last] = value
                self.n += 1
                link = Link.parse(ITEM) if source == "plane" else Link("gitea_pr", "sdlc/example-webapp#3")
                key = self.svc.links.thread_of(link) or str(link)
                self.queue.put(source, delivery or f"{name}-{self.n}", key, d["body"], d["headers"])
                self.drain()

            def finish(self, result):
                run = list(self.svc.executor.runs.values())[-1]
                self.queue.put(
                    "job",
                    f"{run.name}/uid",
                    run.thread_id,
                    {"job": run.name, "thread_id": run.thread_id, "succeeded": True, "result": result},
                )
                self.drain()

            def preview(self, served, commit=None):
                """The watcher's word on the thread's preview: it serves the commit, or the wait is over."""
                thread_id = self.svc.links.thread_of(Link.parse(ITEM))
                url, watched = self.svc.previews.waits.pop(thread_id)
                delivery_id, payload = event(thread_id, commit or watched, served, since=str(self.n))
                self.queue.put("preview", delivery_id, thread_id, payload)
                self.drain()
                return url

            def drain(self):
                while self.queue.process(self.dispatch):
                    pass
                with pool.connection() as conn:
                    errors = conn.execute("SELECT error FROM inbox WHERE error IS NOT NULL").fetchall()
                assert not errors, errors

            @property
            def item(self):
                return self.dispatch.item(self.svc.links.thread_of(Link.parse(ITEM)))

            @property
            def runs(self):
                return list(self.svc.executor.runs)

        yield World()


def assign(world):
    world.hook("plane", "issue_assigned_to_agent")


def test_from_assignment_to_a_merge_into_development(world):
    chat = world.svc.chat.answers
    assign(world)
    assert world.item.phase == "Drafting" and world.item.key == "EWA-1"
    assert world.item.ticket.branch == "feat/EWA-1_orders-filter-by-status" and world.runs == ["ewa-1-propose-1"]
    run = world.svc.executor.runs["ewa-1-propose-1"]
    assert (
        run.task["repo"] == "sdlc/example-webapp"
        and run.task["base"] == "development"
        and "npm test" in run.task["checks"]
    )

    world.finish({"result": "spec_ready", "commit": "a1"})
    assert world.item.phase == "Spec review" and world.item.pr == 3
    assert world.svc.links.thread_of(Link("gitea_pr", "sdlc/example-webapp#3")) == world.svc.links.thread_of(
        Link.parse(ITEM)
    )
    assert "https://gitea.sdlc.internal/sdlc/example-webapp/pulls/3" in world.svc.tracker.comments[-1][1]

    chat["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Implementing" and world.runs[-1] == "ewa-1-apply-2"

    world.finish({"result": "code_ready", "commit": "b2"})
    assert world.item.phase == "Deploying to QA" and world.svc.code_host.labels == [(3, "preview")]
    assert "https://ewa-1.qa.example-webapp.sdlc.internal" in world.svc.code_host.comments[-1][2]
    url = world.preview(served=True)
    assert url == "http://ewa-1.example-webapp-preview.svc/version.json"
    assert world.item.phase == "Code review" and "its preview is up" in world.svc.code_host.comments[-1][2]

    chat["@agent ok, merge it into dev"] = ("merge", 0.9)
    world.hook("gitea", "review_approved", sender__login="sergey")
    assert world.runs[-1] == "ewa-1-archive-3" and not world.svc.code_host.merged, "archive first, then merge"

    world.finish({"result": "archived", "commit": "c3"})
    assert world.item.phase == "On dev" and world.svc.code_host.merged == [3]
    assert world.svc.tracker.statuses == [
        "Drafting",
        "Spec review",
        "Implementing",
        "Deploying to QA",
        "Code review",
        "On dev",
    ]
    chat["@agent looks good, go ahead"] = ("archive", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.svc.tracker.comments[-1][1] == "The change of EWA-1 is archived already."
    chat["@agent looks good, go ahead"] = ("ask", 0.9)
    world.hook("plane", "comment_mention_agent")
    facts = world.svc.reply.facts[-1]
    assert facts.archived and facts.may_ask == ["prepare a release"], "no archive of an archived change"


def test_questions_stop_the_work_and_an_answer_resumes_it(world):
    assign(world)
    world.finish({"result": "questions", "questions": ["Should the filter survive a reload?"]})
    assert world.item.phase == "Needs info" and "survive a reload" in world.svc.tracker.comments[-1][1]
    world.svc.chat.answers["@agent looks good, go ahead"] = ("ask", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Needs info" and world.runs == ["ewa-1-propose-1"], "a question is no answer"
    assert world.svc.reply.facts[-1].waiting_for == ["Should the filter survive a reload?"]
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Drafting" and world.runs[-1] == "ewa-1-propose-2"
    answers = world.svc.executor.runs["ewa-1-propose-2"].task["answers"]
    assert answers[0]["question"] == "Should the filter survive a reload?" and "go ahead" in answers[0]["answer"]


def test_changes_requested_on_the_code_take_the_reviews_inline_comments(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent")
    world.finish({"result": "code_ready"})
    world.svc.chat.answers["@agent please keep the filter after reload"] = ("request_changes", 0.9)
    world.hook("gitea", "review_request_changes", sender__login="sergey")
    assert world.item.phase == "Implementing" and world.runs[-1] == "ewa-1-fix-3"
    feedback = world.svc.executor.runs["ewa-1-fix-3"].task["feedback"]
    assert "keep the filter after reload" in feedback and "src/orders.ts:4" in feedback
    assert world.svc.code_host.comments[-1][2] == "Implementing a fix.", "the reply goes where it was asked"


def test_a_person_without_the_role_and_a_wrong_phase_are_refused(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent ok, merge it into dev"] = ("merge", 0.9)
    world.hook("gitea", "review_approved", sender__login="stranger")
    assert world.svc.code_host.comments[-1][2] == "I only take requests from the people of example-webapp."
    assert world.svc.chat.calls == 0, "no model reads a stranger's comment"
    world.hook("gitea", "review_approved", sender__login="sergey")
    assert world.svc.code_host.comments[-1][2] == "EWA-1 is in Spec review, so I cannot merge it now."
    assert world.item.phase == "Spec review" and not world.svc.code_host.merged


def test_an_unclear_comment_gets_a_question_and_changes_nothing(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.4)
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Spec review" and world.runs == ["ewa-1-propose-1"]
    assert world.svc.tracker.comments[-1][1] == "unclear: EWA-1 is in Spec review"
    facts = world.svc.reply.facts[-1]
    assert facts.unclear and "approve it" in facts.may_ask and "merge it" not in facts.may_ask


def test_a_question_gets_an_answer_from_the_facts_and_changes_nothing(world):
    assign(world)
    world.svc.chat.answers["@agent looks good, go ahead"] = ("ask", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.svc.tracker.comments[-1][1] == "answer: EWA-1 is in Drafting", "asked while the run goes on"
    world.finish({"result": "spec_ready"})
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Spec review" and world.runs == ["ewa-1-propose-1"]
    facts = world.svc.reply.facts[-1]
    assert facts.comment == "@agent looks good, go ahead" and not facts.unclear
    assert facts.pull_request == "https://gitea.sdlc.internal/sdlc/example-webapp/pulls/3"
    assert {"approve it", "take changes to it", "reject it"} <= set(facts.may_ask)
    assert "answer a question" not in facts.may_ask and facts.conversation[-1].author == "agent"


def test_a_status_set_to_closed_rejects_the_item(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.hook("plane", "issue_state_closed")
    assert world.item.phase == "Closed" and world.svc.code_host.closed == [3]


def test_a_person_merging_before_the_archive_gets_a_note_and_an_archive_pr_on_request(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent")
    world.finish({"result": "code_ready"})
    world.hook("gitea", "pr_merged", sender__login="sergey")
    assert world.item.phase == "On dev"
    assert (
        "were not archived" in world.svc.tracker.comments[-1][1] and "#3 on EWA-1" in world.svc.tracker.comments[-1][1]
    )
    world.svc.chat.answers["@agent looks good, go ahead"] = ("archive", 0.95)
    world.hook("plane", "comment_mention_agent")
    assert world.runs[-1] == "ewa-1-archive-3"
    assert world.svc.executor.runs["ewa-1-archive-3"].task["branch"] == "archive/EWA-1"
    world.finish({"result": "archived"})
    assert ("archive/EWA-1", "development") in world.svc.code_host.prs.values()
    assert "archive pull request is open" in world.svc.tracker.comments[-1][1]
    world.hook("gitea", "comment_mention_agent", issue__number=4, sender__login="sergey")  # on the archive PR
    assert world.svc.code_host.comments[-1][1:] == (4, "The change of EWA-1 is archived already.")


def test_a_delivery_applied_before_changes_nothing_and_a_restart_keeps_the_phase(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.fresh()  # the orchestrator restarted while the item waits in Spec review
    assert world.item.phase == "Spec review"
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent", delivery="same")
    with world.svc.queue.pool.connection() as conn:  # the same delivery again, as a replay after a crash
        conn.execute("UPDATE inbox SET processed_at = NULL, delivery_id = 'same' WHERE delivery_id = 'same'")
    world.drain()
    assert world.runs == ["ewa-1-propose-1", "ewa-1-apply-2"] and world.item.phase == "Implementing"


def test_events_of_unconnected_projects_and_untracked_items_are_left_alone(world):
    world.hook("plane", "comment_mention_agent")  # no thread works on the item yet
    world.hook("gitea", "comment_mention_agent", repository__full_name="sdlc/other")
    assert world.runs == [] and world.svc.tracker.comments == []


def test_the_thread_api_shows_a_thread_and_its_messages(world, pool):
    from fastapi.testclient import TestClient

    from orchestrator.api import create_app

    assign(world)
    world.finish({"result": "spec_ready"})
    client = TestClient(create_app(pool, world.queue, world.svc.links, world.dispatch.view, {}))
    thread_id = client.get("/threads", params={"link": "gitea_pr:sdlc/example-webapp#3"}).json()["thread_id"]
    shown = client.get(f"/threads/{thread_id}").json()
    assert (
        shown["values"]["phase"] == "Spec review" and shown["values"]["key"] == "EWA-1" and shown["values"]["pr"] == 3
    )
    assert shown["values"]["branch"] == "feat/EWA-1_orders-filter-by-status"
    assert set(shown["links"]) == {ITEM, "gitea_pr:sdlc/example-webapp#3", "ticket:example-webapp/EWA-1"}
    messages = client.get(f"/threads/{thread_id}/messages").json()["messages"]
    assert [m["author"] for m in messages] == ["agent", "agent"] and "spec is ready" in messages[-1]["text"]
    assert client.get("/threads/nope").status_code == 404


def to_code_review(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent")
    world.finish({"result": "code_ready"})


def test_one_run_at_a_time_and_a_late_result_counts_no_more(world):
    to_code_review(world)
    world.svc.chat.answers["@agent ok, merge it into dev"] = ("merge", 0.9)
    world.hook("gitea", "review_approved", sender__login="sergey")
    world.svc.chat.answers["@agent looks good, go ahead"] = ("merge", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.runs[-1] == "ewa-1-archive-3" and "still on the archive run" in world.svc.tracker.comments[-1][1]
    world.hook("plane", "issue_state_closed")
    assert world.svc.executor.cancelled == ["ewa-1-archive-3"]
    world.finish({"result": "archived"})  # the archive run ends after the item was closed
    assert world.item.phase == "Closed" and not world.svc.code_host.merged
    assign(world)  # assigned again: new runs get new names
    assert world.item.phase == "Drafting" and world.runs[-1] == "ewa-1-propose-4"


def test_a_failed_run_waits_in_needs_info_and_the_next_mention_retries_it(world):
    assign(world)
    world.finish({"result": "failed", "reason": "the run changed nothing it may commit"})
    assert world.item.phase == "Needs info" and "Reply with @agent to run it again" in world.svc.tracker.comments[-1][1]
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.9)  # no question: the answer
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Drafting" and world.runs[-1] == "ewa-1-propose-2" and world.svc.chat.calls == 1


def test_changes_after_the_archive_take_it_back_and_a_review_verdict_wins_over_its_text(world):
    to_code_review(world)
    world.hook("gitea", "review_approved", sender__login="sergey")
    world.finish({"result": "archived"})
    assert world.item.archived
    world.svc.chat.answers["@agent please keep the filter after reload"] = ("approve", 0.9)
    world.hook("gitea", "review_request_changes", sender__login="sergey")
    assert world.runs[-1] == "ewa-1-fix-4" and world.svc.executor.runs["ewa-1-fix-4"].task["unarchive"]
    world.finish({"result": "failed", "reason": "the run changed nothing it may commit"})
    world.hook("plane", "comment_mention_agent")  # the retry takes the archive commit back too
    assert world.runs[-1] == "ewa-1-fix-5" and world.svc.executor.runs["ewa-1-fix-5"].task["unarchive"]
    world.finish({"result": "code_ready"})
    world.hook("gitea", "review_approved", sender__login="sergey")
    assert world.runs[-1] == "ewa-1-archive-6", "archived again after the fix"


def test_a_status_the_agent_refuses_is_set_back(world):
    assign(world)
    world.hook("plane", "issue_state_approved")  # Approved while the spec is being drafted
    assert world.svc.tracker.statuses == ["Drafting", "Drafting"] and world.item.phase == "Drafting"
    assert "cannot approve it now" in world.svc.tracker.comments[-1][1]


def test_a_run_result_without_the_asked_shape_counts_as_a_failed_run(world):
    assign(world)
    world.finish({"result": None, "questions": [{"q": "?"}]})
    assert world.item.phase == "Needs info" and world.item.running is None
    assert "no usable shape" in world.svc.tracker.comments[-1][1]


def test_an_approved_status_that_leaves_the_phase_is_set_back(world):
    to_code_review(world)
    world.hook("plane", "issue_state_approved")  # the code approved by setting the status
    assert world.runs[-1] == "ewa-1-archive-3" and world.svc.tracker.statuses[-1] == "Code review"


def test_closing_an_item_that_waits_for_answers_drops_the_questions(world):
    assign(world)
    world.finish({"result": "questions", "questions": ["Should the filter survive a reload?"]})
    world.hook("plane", "issue_state_closed")
    assert world.item.phase == "Closed" and world.item.paused is None and world.item.summary()["questions"] == []


def test_a_rejection_in_needs_info_closes_the_item_instead_of_answering(world):
    assign(world)
    world.finish({"result": "failed", "reason": "the run wrote no change"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("reject", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.item.phase == "Closed" and world.runs == ["ewa-1-propose-1"], "no run again"


def test_a_long_title_is_cut_after_a_whole_word():
    from orchestrator.machine import slug

    assert slug("Add a title attribute to the footer commit") == "add-a-title-attribute-to-the-footer"
    assert slug("Orders: filter by status!") == "orders-filter-by-status"
    assert slug("x" * 50) == "x" * 40 and slug("???") == "change"


def test_a_status_set_by_hand_follows_the_work_again(world):
    assign(world)
    world.hook("plane", "issue_state_approved", data__state__name="Code review")  # dragged on the board
    assert world.svc.tracker.statuses[-1] == "Drafting"


def test_a_run_result_that_fails_its_last_attempt_stops_the_wait_for_the_run(world):
    to_code_review(world)
    world.svc.chat.answers["@agent ok, merge it into dev"] = ("merge", 0.9)
    world.hook("gitea", "review_approved", sender__login="sergey")
    thread = world.svc.links.thread_of(Link.parse(ITEM))

    def refused(repo, number):
        raise RuntimeError("405 the pull request cannot be merged")

    world.svc.code_host.merge = refused
    result = {"job": "ewa-1-archive-3", "thread_id": thread, "result": {"result": "archived"}}
    for attempt in (1, 5):  # an earlier attempt leaves it to the retry; the last one gives up
        with pytest.raises(RuntimeError):
            world.dispatch(Delivery(0, "job", "ewa-1-archive-3/uid", thread, {}, result, attempt))
        assert (world.item.running is None) is (attempt == 5)
    assert "could not finish a step" in world.svc.tracker.comments[-1][1]
    world.svc.chat.answers["@agent looks good, go ahead"] = ("merge", 0.9)
    world.hook("plane", "comment_mention_agent")
    assert world.runs[-1] == "ewa-1-archive-4", "people can act again"


def test_a_retry_acts_on_the_first_reading_of_a_comment(world):
    assign(world)
    world.finish({"result": "spec_ready"})
    executor, chat = world.svc.executor, world.svc.chat
    chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)

    def down(run):
        raise RuntimeError("the Kubernetes API does not answer")

    executor.start, start = down, executor.start
    with pytest.raises(AssertionError):  # the attempt fails after the comment was read
        world.hook("plane", "comment_mention_agent")
    executor.start = start
    chat.answers["@agent looks good, go ahead"] = ("request_changes", 0.95)  # a model that reads it anew
    with world.svc.queue.pool.connection() as conn:
        conn.execute("UPDATE inbox SET available_at = now()")  # the retry, without its backoff
    world.drain()
    assert world.runs[-1] == "ewa-1-apply-2" and chat.calls == 1, "approved, as read the first time"


def test_an_event_recorded_under_its_object_after_the_thread_was_linked_waits_in_the_threads_order(world):
    assign(world)
    thread = world.svc.links.thread_of(Link.parse(ITEM))
    d = json.loads((FIXTURES / "plane" / "comment_mention_agent.json").read_text())
    world.queue.put("plane", "late", ITEM, d["body"], d["headers"])  # routed before the link was seen
    assert world.queue.process(world.dispatch)  # taken, but not run beside the thread's events
    with world.svc.queue.pool.connection() as conn:
        key, attempts, done = conn.execute(
            "SELECT routing_key, attempts, processed_at IS NOT NULL FROM inbox WHERE delivery_id = 'late'"
        ).fetchone()
    assert (key, attempts, done) == (thread, 0, False), "moved to the thread's key, handed back untried"
    world.drain()
    with world.svc.queue.pool.connection() as conn:
        assert conn.execute("SELECT processed_at IS NOT NULL FROM inbox WHERE delivery_id = 'late'").fetchone()[0]


def to_deploying(world, commit="b2"):
    assign(world)
    world.finish({"result": "spec_ready"})
    world.svc.chat.answers["@agent looks good, go ahead"] = ("approve", 0.95)
    world.hook("plane", "comment_mention_agent")
    world.finish({"result": "code_ready", "commit": commit})
    assert world.item.phase == "Deploying to QA"


def test_a_preview_that_does_not_come_up_sends_the_code_to_review_with_the_reason(world):
    to_deploying(world)
    world.svc.code_host.builds["b2"] = Build(
        status="failure", url="https://gitea.sdlc.internal/sdlc/example-webapp/actions/runs/7"
    )
    world.preview(served=False)
    said = world.svc.code_host.comments[-1][2]
    assert world.item.phase == "Code review"
    assert "did not serve b2 within 15 minutes" in said and "failure: https://gitea.sdlc.internal" in said


def test_a_fix_waits_for_its_own_commit_in_the_preview_and_a_stale_check_is_dropped(world):
    to_deploying(world)
    world.preview(served=True)
    world.svc.chat.answers["@agent please keep the filter after reload"] = ("request_changes", 0.9)
    world.hook("gitea", "review_request_changes", sender__login="sergey")
    world.finish({"result": "code_ready", "commit": "c3"})
    assert world.item.phase == "Deploying to QA" and world.item.deploying.commit == "c3"
    thread_id = world.svc.links.thread_of(Link.parse(ITEM))
    delivery_id, payload = event(thread_id, "b2", True, since="late")
    world.queue.put("preview", delivery_id, thread_id, payload)
    world.drain()
    assert world.item.phase == "Deploying to QA", "a check of the old commit does not count"
    world.preview(served=True)
    assert (
        world.item.phase == "Code review"
        and "The fix is ready, and its preview is up" in world.svc.code_host.comments[-1][2]
    )


def test_closing_an_item_that_waits_for_its_preview_ends_the_wait(world):
    to_deploying(world)
    world.hook("plane", "issue_state_closed")
    thread_id = world.svc.links.thread_of(Link.parse(ITEM))
    assert world.item.phase == "Closed" and world.item.deploying is None
    assert world.svc.previews.forgotten == [thread_id] and thread_id not in world.svc.previews.waits


def test_a_failed_lookup_of_the_build_does_not_keep_the_code_from_review(world):
    to_deploying(world)

    def down(repo, commit):
        raise RuntimeError("gitea is down")

    world.svc.code_host.build = down
    world.preview(served=False)
    assert world.item.phase == "Code review"
    assert "CI's run of it could not be looked up" in world.svc.code_host.comments[-1][2]
