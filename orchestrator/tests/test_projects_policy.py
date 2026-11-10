"""Project files and people (task 7.4), the model client, intents and the policy (task 7.5)."""

import os
from pathlib import Path

import pytest

from orchestrator import intents
from orchestrator.machine import POLICY
from orchestrator.policy import OPEN, Phase
from orchestrator.projects import LiveProjects, Projects

REPO_PROJECTS = Path(__file__).parents[2] / "platform" / "projects"
decide = POLICY.decide


@pytest.fixture(scope="module")
def projects():
    return Projects.load(REPO_PROJECTS)


def test_the_repositorys_project_files_load_and_pass_the_schema(projects):
    ewa = projects.by_tracker("sdlc", "EWA")
    assert ewa.name == "example-webapp" and ewa.repo == "sdlc/example-webapp" and ewa.base == "development"
    assert projects.by_repo("sdlc/example-webapp") is ewa
    assert "npm test" in ewa.checks


def test_a_person_has_the_same_roles_in_every_tool(projects):
    ewa = projects.by_repo("sdlc/example-webapp")
    in_plane, in_gitea = ewa.person("plane", "admin"), ewa.person("gitea", "gitea_admin")
    assert in_plane is in_gitea and in_plane.roles == {"product_owner", "engineer"}


def test_an_unknown_account_is_nobody(projects):
    ewa = projects.by_repo("sdlc/example-webapp")
    assert ewa.person("gitea", "stranger") is None and ewa.person("plane", "") is None
    assert decide("approve", Phase.SPEC_REVIEW, None, "example-webapp").reason == \
        "I only take requests from the people of example-webapp."


def test_an_unconnected_project_or_repository_is_not_found(projects):
    assert projects.by_repo("sdlc/other") is None and projects.by_tracker("sdlc", "XYZ") is None


PO, ENG = frozenset({"product_owner"}), frozenset({"engineer"})


@pytest.mark.parametrize("intent, phase, roles, allowed", [
    ("approve", Phase.SPEC_REVIEW, PO, True),
    ("approve", Phase.SPEC_REVIEW, ENG, True),
    ("approve", Phase.CODE_REVIEW, ENG, True),
    ("approve", Phase.CODE_REVIEW, PO, False),
    ("request_changes", Phase.SPEC_REVIEW, PO, True),
    ("request_changes", Phase.CODE_REVIEW, PO, True),
    ("request_changes", Phase.IMPLEMENTING, ENG, False),
    ("reject", Phase.DRAFTING, PO, True),
    ("reject", Phase.CODE_REVIEW, ENG, True),
    ("reject", Phase.ON_DEV, ENG, False),
    ("answer", Phase.NEEDS_INFO, frozenset(), True),
    ("answer", Phase.SPEC_REVIEW, PO, False),
    ("merge", Phase.CODE_REVIEW, ENG, True),
    ("merge", Phase.CODE_REVIEW, PO, False),
    ("merge", Phase.SPEC_REVIEW, ENG, False),
    ("archive", Phase.ON_DEV, ENG, True),
    ("archive", Phase.ON_DEV, PO, False),
    ("prepare_release", None, PO, True),
    ("prepare_release", Phase.RELEASED, ENG, True),
    ("prepare_release", None, frozenset(), False),
])
def test_every_row_of_the_policy(intent, phase, roles, allowed):
    assert decide(intent, phase, roles, "example-webapp", "EWA-12").allowed is allowed


def test_reject_works_in_every_open_phase():
    assert all(decide("reject", phase, PO, "p").allowed for phase in OPEN)


def test_refusals_say_why():
    assert decide("merge", Phase.CODE_REVIEW, PO, "example-webapp").reason == \
        "Only an engineer of example-webapp can merge it in Code review."
    assert decide("merge", Phase.SPEC_REVIEW, ENG, "example-webapp", "EWA-12").reason == \
        "EWA-12 is in Spec review, so I cannot merge it now."


@pytest.mark.parametrize("kind, data, intent", [
    ("status", {"state": "Approved"}, "approve"),
    ("status", {"state": "Closed"}, "reject"),
    ("status", {"state": "Drafting"}, None),
    ("review", {"review": "approved"}, "approve"),
    ("review", {"review": "request_changes"}, "request_changes"),
    ("review", {"review": "comment"}, None),
    ("comment", {}, None),
])
def test_explicit_signals_need_no_model(kind, data, intent):
    from orchestrator.policy import signal

    assert signal(kind, data) == intent


class Shapeless:
    """A chat model whose structured call returns nothing or garbage."""

    def __init__(self, answer):
        self.answer = answer

    def with_structured_output(self, schema, method):
        return self

    def invoke(self, prompt):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("answer", [None, ValueError("confidence: input should be <= 1")])
def test_a_reading_without_the_asked_shape_is_none(answer):
    assert intents.read(Shapeless(answer), "@agent hmm", "Spec review") == intents.Intent("none", 0.0)


def test_the_agent_image_comes_from_the_orchestrators_commit(monkeypatch):
    from orchestrator import config

    monkeypatch.setenv("ORCHESTRATOR_COMMIT", "abc123")
    assert config.load("/nonexistent").agent_image == "gitea.sdlc.internal/platform/agent-runner:abc123"


def test_the_model_client_is_chosen_by_the_models_prefix():
    from langchain_deepseek import ChatDeepSeek
    from langchain_openai import ChatOpenAI

    assert isinstance(intents.chat_model("deepseek/deepseek-v4-flash-0731", "https://x/v1", "k"), ChatDeepSeek)
    assert isinstance(intents.chat_model("z-ai/glm-5.2", "https://x/v1", "k"), ChatOpenAI)


def test_an_unsure_reading_or_none_is_not_acted_on():
    assert intents.clear(intents.Intent("approve", 0.9), 0.7)
    assert not intents.clear(intents.Intent("approve", 0.5), 0.7)
    assert not intents.clear(intents.Intent("none", 1.0), 0.7)


@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="a live call needs LLM_API_KEY")
def test_live_a_plain_approval_reads_as_approve():
    chat = intents.chat_model(os.environ.get("INTENT_MODEL", "deepseek/deepseek-v4-flash-0731"),
                              os.environ.get("LLM_BASE_URL", "https://routerai.ru/api/v1"), os.environ["LLM_API_KEY"])
    reading = intents.read(chat, "@agent looks good, go ahead", "Spec review", "The spec is ready for review.")
    assert reading.name == "approve" and reading.confidence >= 0.7


def test_an_event_of_an_unconnected_project_or_repository_has_no_project(projects):
    import json

    from conftest import FIXTURES
    from orchestrator.adapters import gitea, plane

    d = json.loads((FIXTURES / "plane" / "comment_mention_agent.json").read_text())
    event = plane.to_event(d["headers"], d["body"], "acf51e1d-b7bc-4e95-a61d-9deba97c5a30")
    assert projects.of_event(event, lambda ws, pid: "EWA").name == "example-webapp"
    assert projects.of_event(event, lambda ws, pid: None) is None  # a Plane project with no file
    d = json.loads((FIXTURES / "gitea" / "comment_mention_agent.json").read_text())
    event = gitea.to_event(d["headers"], d["body"])
    assert projects.of_event(event, None).name == "example-webapp"
    d["body"]["repository"]["full_name"] = "sdlc/other"
    assert projects.of_event(gitea.to_event(d["headers"], d["body"]), None) is None


def test_live_projects_follow_their_files_and_keep_the_last_good_ones(tmp_path):
    repo = Path(__file__).parents[2] / "platform" / "projects"
    file = tmp_path / "example-webapp.yaml"
    (tmp_path / "project.schema.json").write_text((repo / "project.schema.json").read_text())
    file.write_text((repo / "example-webapp" / "project.yaml").read_text())
    projects = LiveProjects(tmp_path)
    assert projects.by_repo("sdlc/example-webapp").base == "development"
    file.write_text(file.read_text().replace("base: development", "base: dev"))
    assert projects.by_repo("sdlc/example-webapp").base == "dev"
    file.write_text("name: broken\n")
    assert projects.by_repo("sdlc/example-webapp").base == "dev"
