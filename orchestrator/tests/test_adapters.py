"""Plane's and Gitea's webhooks become events (task 7.3), on the fixtures from spikes 1.3 and 1.4."""

import json

import pytest
from conftest import FIXTURES

from orchestrator.adapters import gitea, plane

AGENT = "acf51e1d-b7bc-4e95-a61d-9deba97c5a30"  # the agent's Plane user id in the fixtures
ITEM = "plane_work_item:72de36e4-09bb-4182-9176-097a5a93da7a/d71d47ba-8b66-45ce-bd4d-8cffb7a1c48a"
PR = "gitea_pr:sdlc/example-webapp#3"


def fixture(source, name, **sender):
    d = json.loads((FIXTURES / source / f"{name}.json").read_text())
    if sender:
        d["body"]["sender"]["login"] = sender["login"]
    return d["headers"], d["body"]


def plane_event(name):
    return plane.to_event(*fixture("plane", name), agent_id=AGENT)


def test_assigning_a_ticket_to_the_agent_is_intake():
    e = plane_event("issue_assigned_to_agent")
    assert (e.kind, str(e.link), e.actor.username) == ("assigned", ITEM, "")
    assert e.actor.id == "525f2c23-8de0-439d-b238-577d889c9918", "people are matched by id, never by display name"
    assert e.data["sequence_id"] == 1 and e.data["title"] == "Orders: filter by status"
    assert e.delivery_id


@pytest.mark.parametrize("name, state", [("issue_state_approved", "Approved"), ("issue_state_closed", "Closed")])
def test_a_status_set_by_a_person_is_a_signal(name, state):
    e = plane_event(name)
    assert (e.kind, e.data["state"]) == ("status", state) and e.actor.id


def test_a_comment_that_mentions_the_agent_is_a_request():
    e = plane_event("comment_mention_agent")
    assert (e.kind, str(e.link), e.mentions_agent) == ("comment", ITEM, True)
    assert e.text == "@agent looks good, go ahead"


@pytest.mark.parametrize("name", ["comment_without_mention", "issue_created", "project_created"])
def test_plane_events_without_a_request_produce_nothing(name):
    assert plane_event(name) is None


@pytest.mark.parametrize("name", ["comment_by_agent", "issue_state_by_agent"])
def test_the_agents_own_plane_events_are_dropped(name):
    assert plane_event(name) is None


def test_plane_routes_comments_and_work_items_to_the_same_link():
    assert str(plane.link_of(*fixture("plane", "comment_mention_agent"))) == ITEM
    assert str(plane.link_of(*fixture("plane", "issue_state_approved"))) == ITEM


def gitea_event(name, **sender):
    return gitea.to_event(*fixture("gitea", name, **sender))


def test_a_pull_request_comment_that_mentions_the_agent_is_a_request():
    e = gitea_event("comment_mention_agent")
    assert (e.kind, str(e.link), e.actor.username, e.text) == ("comment", PR, "eng", "@agent looks good, go ahead")


@pytest.mark.parametrize(
    "name, review, mentioned",
    [
        ("review_approved", "approved", True),
        ("review_request_changes", "request_changes", True),
    ],
)
def test_reviews_are_signals_and_may_carry_a_request(name, review, mentioned):
    e = gitea_event(name)
    assert (e.kind, e.data["review"], e.mentions_agent, str(e.link)) == ("review", review, mentioned, PR)
    assert e.data["head_sha"], "the reviewed commit is the head the event names"


def test_an_approval_that_asks_to_merge_carries_both():
    e = gitea_event("review_approved")
    assert e.data["review"] == "approved" and "merge it into dev" in e.text


def test_a_review_comment_without_a_mention_is_nothing():
    assert gitea_event("review_comment") is None


@pytest.mark.parametrize("name, kind", [("pr_merged", "pr_merged"), ("pr_closed_unmerged", "pr_closed")])
def test_a_person_merging_or_closing_a_pull_request(name, kind):
    e = gitea_event(name, login="eng")
    assert (e.kind, e.data["head"], e.actor.username) == (
        kind,
        "feat/EWA-12_filter" if kind == "pr_merged" else "feat/EWA-13_drop",
        "eng",
    )


@pytest.mark.parametrize("name", ["comment_by_agent", "pr_merged", "pr_opened", "pr_synchronized"])
def test_the_agents_own_gitea_events_are_dropped(name):
    assert gitea_event(name) is None


def test_gitea_events_without_a_request_produce_nothing():
    assert gitea_event("comment_without_mention") is None


def test_a_published_release_lists_its_tickets():
    e = gitea_event("release_published")
    assert (e.kind, e.data["tag"], e.data["tickets"]) == ("release", "v0.1.0", ["EWA-12"])


def test_the_mention_is_a_whole_word():
    headers, body = fixture("gitea", "comment_mention_agent")
    for text in ("mail agent@example.com or ping @agents", "ask @agent-runner"):
        body["comment"]["body"] = text
        assert gitea.to_event(headers, body) is None


def test_an_edited_or_deleted_comment_is_not_a_request():
    headers, body = fixture("gitea", "comment_mention_agent")
    for action in ("edited", "deleted"):
        body["action"] = action
        assert gitea.to_event(headers, body) is None
