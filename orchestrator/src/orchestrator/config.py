"""Settings: one typed model each for `migrate` and `serve`, validated at start. The environment
holds secrets and connections and wins over config.yaml (ORCHESTRATOR_CONFIG), which holds the
rest; a variable is the setting's name in capitals (DATABASE_URL, WORKERS, ...)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
    YamlConfigSettingsSource,
)

AGENT_RUNNER = "gitea.sdlc.internal/platform/agent-runner"


class ConfigError(Exception):
    """Settings missing or malformed; the message names each setting."""


class MigrateSettings(BaseSettings):
    """What `orchestrator migrate` needs; the file's other settings are serve's."""

    # An empty variable (a Secret's key left blank) counts as not set, so the file or the default holds.
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=True)

    database_url: SecretStr

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """The environment over config.yaml; no .env file and no secrets directory."""
        config_file = Path(os.environ.get("ORCHESTRATOR_CONFIG", "/config/config.yaml"))
        return init_settings, env_settings, YamlConfigSettingsSource(settings_cls, yaml_file=config_file)


Fraction = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=1)]


class Settings(MigrateSettings):
    """What `orchestrator serve` needs. A key in the file it does not know is an error, so a typo
    never passes for a default."""

    model_config = SettingsConfigDict(extra="forbid", env_ignore_empty=True)

    # Secrets: required, so a pod without one stops at start instead of failing every call.
    llm_api_key: SecretStr
    # The model provider (any OpenAI-compatible API) and the models for reading intents and for runs.
    llm_base_url: str = "https://routerai.ru/api/v1"
    intent_model: str = "deepseek/deepseek-v4-flash-0731"
    agent_model: str = "llm/deepseek/deepseek-v4-flash-0731"
    intent_threshold: Fraction = 0.7
    workers: Count = 2
    max_attempts: Count = 5
    max_active_runs: Count = 2
    projects_dir: Path = Path("/projects")
    agent_namespace: str = "sdlc-agents"
    # The agent image; by default agent-runner of the orchestrator's own commit (design decision 3).
    orchestrator_commit: str = "unknown"
    agent_image: str = ""
    plane_url: str = "http://plane-api.plane.svc:8000"
    plane_api_key: SecretStr
    plane_people: dict[str, str] = {}  # username -> Plane user id, from the Secret sdlc/plane-agent
    gitea_url: str = "http://gitea-http.gitea.svc:3000"
    gitea_public_url: str = "https://gitea.sdlc.internal"  # for the addresses people open
    gitea_token: SecretStr
    plane_webhook_secret: SecretStr
    gitea_webhook_secret: SecretStr

    @model_validator(mode="after")
    def _agent_image_of_the_commit(self) -> Self:
        if not self.agent_image and self.orchestrator_commit != "unknown":
            self.agent_image = f"{AGENT_RUNNER}:{self.orchestrator_commit}"
        return self


def load[S: MigrateSettings](kind: type[S]) -> S:
    """The settings, or a ConfigError naming every setting that is missing or malformed."""
    try:
        return kind()
    except ValidationError as error:
        problems = [f"{'.'.join(map(str, e['loc'])).upper() or '(settings)'}: {e['msg']}" for e in error.errors()]
        raise ConfigError("; ".join(problems)) from None
    except SettingsError as error:  # a variable that is not the JSON its setting needs
        raise ConfigError(str(error)) from None
    except yaml.YAMLError as error:
        raise ConfigError(f"{os.environ.get('ORCHESTRATOR_CONFIG', '/config/config.yaml')}: {error}") from None
