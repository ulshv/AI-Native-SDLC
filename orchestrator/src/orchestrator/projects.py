"""Target projects: platform/projects/<name>/project.yaml, checked against project.schema.json
(design decision 10), and the people in them with their roles (design decision 5).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .events import Event

log = logging.getLogger(__name__)
ROLES = ("product_owner", "engineer")


@dataclass(frozen=True)
class Person:
    name: str
    roles: frozenset[str]
    plane: str | None = None
    gitea: str | None = None


@dataclass(frozen=True)
class Project:
    name: str
    workspace: str
    identifier: str
    tracker_name: str
    repo: str
    base: str
    release: str
    domain: str
    previews: bool
    checks: tuple[str, ...]
    people: tuple[Person, ...]

    def person(self, tool: str, username: str) -> Person | None:
        """The person behind an account, so one person has the same roles in every tool."""
        for person in self.people:
            if username and getattr(person, tool, None) == username:
                return person
        return None


class Projects:
    def __init__(self, projects: list[Project]):
        self._projects = projects

    @property
    def all(self) -> list[Project]:
        return self._projects

    @classmethod
    def load(cls, directory: Path) -> Projects:
        return Projects(cls.read(directory))

    @classmethod
    def read(cls, directory: Path) -> list[Project]:
        """Every project file in the directory: platform/projects/*/project.yaml in the
        repository, or the flat ConfigMap target-projects (<name>.yaml) in the cluster."""
        directory = Path(directory)
        schema_file = directory / "project.schema.json"
        schema = json.loads(schema_file.read_text()) if schema_file.exists() else None
        files = sorted(directory.glob("*/project.yaml")) or sorted(directory.glob("*.yaml"))
        projects: list[Project] = []
        for path in files:
            doc = yaml.safe_load(path.read_text())
            if schema:
                jsonschema.validate(doc, schema)
            projects.append(cls.parse(doc))
        return projects

    @staticmethod
    def parse(doc: dict[str, Any]) -> Project:
        return Project(
            name=doc["name"],
            workspace=doc["tracker"]["workspace"],
            identifier=doc["tracker"]["identifier"],
            tracker_name=doc["tracker"]["name"],
            repo=doc["repository"]["gitea"],
            base=doc["repository"]["base"],
            release=doc["repository"]["release"],
            domain=doc["domain"],
            previews=doc["previews"],
            checks=tuple(doc.get("checks", [])),
            people=tuple(
                Person(p["name"], frozenset(p.get("roles", [])), p.get("plane"), p.get("gitea"))
                for p in doc.get("people", [])
            ),
        )

    def by_name(self, name: str) -> Project | None:
        return next((p for p in self.all if p.name == name), None)

    def by_repo(self, repo: str) -> Project | None:
        return next((p for p in self.all if p.repo == repo), None)

    def by_tracker(self, workspace: str, identifier: str) -> Project | None:
        return next((p for p in self.all if p.workspace == workspace and p.identifier == identifier), None)

    def of_event(self, event: Event, identifier_of: Callable[[str, str], str | None]) -> Project | None:
        """The connected project an event belongs to; None for a tracker project or a repository
        that has no project file, whose events the loop leaves alone. identifier_of(workspace,
        project_id) names a tracker project (the tracker's webhooks carry only its id)."""
        if event.source == "gitea":
            return self.by_repo(event.data.get("repo", ""))
        if event.source == "plane":
            workspace = event.data.get("workspace") or ""
            identifier = identifier_of(workspace, event.data.get("project_id") or "") if workspace else None
            return self.by_tracker(workspace, identifier) if identifier else None
        return None


class LiveProjects(Projects):
    """Projects that follow their directory: in the cluster, the mounted ConfigMap target-projects,
    which the kubelet updates in place when a project file changes in git. The files are looked
    at every CHECK_EVERY seconds at most; a file that does not load leaves the projects as they
    were."""

    CHECK_EVERY = 5.0

    def __init__(self, directory: Path, clock: Callable[[], float] = time.monotonic):
        super().__init__(Projects.read(directory))
        self.directory, self.clock = Path(directory), clock
        self._lock = threading.Lock()
        self._seen, self._checked = self._signature(), clock()
        log.info("projects: %s", ", ".join(p.name for p in self._projects) or "none")

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = sorted(
            {
                *self.directory.glob("*/project.yaml"),
                *self.directory.glob("*.yaml"),
                *self.directory.glob("project.schema.json"),
            }
        )
        return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)

    @property
    def all(self) -> list[Project]:
        with self._lock:
            if self.clock() - self._checked >= self.CHECK_EVERY:
                self._checked = self.clock()
                self._reload_if_changed()
            return self._projects

    def _reload_if_changed(self) -> None:
        try:
            seen = self._signature()
        except OSError:  # the kubelet swaps the files as they are listed: look again next time
            log.info("the project files in %s change as they are read", self.directory)
            return
        if seen == self._seen:
            return
        self._seen = seen
        try:
            self._projects = Projects.read(self.directory)
            log.info("projects reloaded: %s", ", ".join(p.name for p in self._projects) or "none")
        except Exception:
            log.exception("the project files in %s do not load; the projects stay as they were", self.directory)
