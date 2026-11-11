"""Settings (task 7.13): validated at start, the environment over config.yaml."""

import pytest

from orchestrator import config

SECRETS = ("LLM_API_KEY", "PLANE_API_KEY", "GITEA_TOKEN", "PLANE_WEBHOOK_SECRET", "GITEA_WEBHOOK_SECRET")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("DATABASE_URL", "ORCHESTRATOR_COMMIT", "AGENT_IMAGE", "WORKERS", *SECRETS):
        monkeypatch.delenv(name, raising=False)
    file = tmp_path / "config.yaml"
    file.write_text("workers: 3\nintent_threshold: 0.8\n")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(file))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/o")
    for name in SECRETS:
        monkeypatch.setenv(name, "secret")
    return monkeypatch


def test_the_environment_wins_over_the_file_and_the_agent_image_follows_the_commit(env):
    env.setenv("WORKERS", "4")
    env.setenv("ORCHESTRATOR_COMMIT", "abc123")
    settings = config.load(config.Settings)
    assert settings.workers == 4 and settings.intent_threshold == 0.8
    assert settings.agent_image == "gitea.sdlc.internal/platform/agent-runner:abc123"


@pytest.mark.parametrize(
    "change, named",
    [
        (lambda env: env.delenv("DATABASE_URL"), "DATABASE_URL: Field required"),
        (lambda env: env.delenv("LLM_API_KEY"), "LLM_API_KEY: Field required"),
        (lambda env: env.setenv("GITEA_WEBHOOK_SECRET", ""), "GITEA_WEBHOOK_SECRET: Field required"),
        (lambda env: env.setenv("WORKERS", "many"), "WORKERS:"),
        (lambda env: env.setenv("INTENT_THRESHOLD", "2"), "INTENT_THRESHOLD:"),
    ],
)
def test_a_missing_or_malformed_setting_is_named(env, change, named):
    change(env)
    with pytest.raises(config.ConfigError, match=named):
        config.load(config.Settings)


def test_migrate_needs_the_database_only(env):
    env.delenv("LLM_API_KEY")
    assert config.load(config.MigrateSettings).database_url.get_secret_value() == "postgresql://u:p@db/o"


def test_a_key_in_the_file_that_serve_does_not_know_is_an_error(env, tmp_path):
    (tmp_path / "config.yaml").write_text("wrokers: 3\n")
    with pytest.raises(config.ConfigError, match="WROKERS: Extra inputs"):
        config.load(config.Settings)


def test_an_empty_variable_counts_as_not_set(env):
    env.setenv("WORKERS", "")
    assert config.load(config.Settings).workers == 3  # the file's


def test_a_variable_that_is_not_its_json_is_named(env):
    env.setenv("PLANE_PEOPLE", "{not json")
    with pytest.raises(config.ConfigError, match="plane_people"):
        config.load(config.Settings)


def test_a_broken_file_is_named(env, tmp_path):
    (tmp_path / "config.yaml").write_text("workers: [3\n")
    with pytest.raises(config.ConfigError, match=r"config\.yaml"):
        config.load(config.Settings)
