"""Settings: the environment for secrets and connections, config.yaml for the rest."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    projects_dir: Path = Path("/projects")
    # The model provider (any OpenAI-compatible API) and the models for reading intents and for runs.
    llm_base_url: str = "https://routerai.ru/api/v1"
    llm_api_key: str = ""
    intent_model: str = "deepseek/deepseek-v4-flash-0731"
    agent_model: str = "llm/deepseek/deepseek-v4-flash-0731"
    intent_threshold: float = 0.7
    workers: int = 2
    max_attempts: int = 5
    max_active_runs: int = 2
    agent_namespace: str = "sdlc-agents"
    agent_image: str = ""
    plane_url: str = "http://plane-api.plane.svc:8000"
    plane_api_key: str = ""
    gitea_url: str = "http://gitea-http.gitea.svc:3000"
    gitea_token: str = ""
    plane_webhook_secret: str = ""
    gitea_webhook_secret: str = ""
    plane_people: str = "{}"  # username -> Plane user id, from the Secret sdlc/plane-agent
    extra: dict = field(default_factory=dict)


def load(config_file: str | None = None) -> Settings:
    """Settings from config.yaml (ORCHESTRATOR_CONFIG) overlaid with the environment."""
    values: dict = {}
    path = Path(config_file or os.environ.get("ORCHESTRATOR_CONFIG", "/config/config.yaml"))
    if path.exists():
        values.update(yaml.safe_load(path.read_text()) or {})
    env = {
        "database_url": "DATABASE_URL",
        "llm_api_key": "LLM_API_KEY",
        "plane_api_key": "PLANE_API_KEY",
        "gitea_token": "GITEA_TOKEN",
        "plane_webhook_secret": "PLANE_WEBHOOK_SECRET",
        "gitea_webhook_secret": "GITEA_WEBHOOK_SECRET",
        "agent_image": "AGENT_IMAGE",
        "projects_dir": "PROJECTS_DIR",
        "plane_people": "PLANE_PEOPLE",
    }
    for key, name in env.items():
        if os.environ.get(name):
            values[key] = os.environ[name]
    if "projects_dir" in values:
        values["projects_dir"] = Path(values["projects_dir"])
    # Agent runs use the agent image of the orchestrator's own commit (design decision 3).
    if not values.get("agent_image") and os.environ.get("ORCHESTRATOR_COMMIT", "unknown") != "unknown":
        values["agent_image"] = f"gitea.sdlc.internal/platform/agent-runner:{os.environ['ORCHESTRATOR_COMMIT']}"
    known = Settings.__dataclass_fields__.keys()
    extra = {k: v for k, v in values.items() if k not in known}
    return Settings(**{k: v for k, v in values.items() if k in known}, extra=extra)
