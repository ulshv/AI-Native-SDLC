"""Target projects: platform/projects/<name>/project.yaml, checked against project.schema.json
(design decision 10), and the people in them with their roles (design decision 5).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import yaml

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
        self.all = projects

    @classmethod
    def load(cls, directory: Path) -> Projects:
        """Every project file in the directory: platform/projects/*/project.yaml in the
        repository, or the flat ConfigMap target-projects (<name>.yaml) in the cluster."""
        directory = Path(directory)
        schema_file = directory / "project.schema.json"
        schema = json.loads(schema_file.read_text()) if schema_file.exists() else None
        files = sorted(directory.glob("*/project.yaml")) or sorted(directory.glob("*.yaml"))
        projects = []
        for path in files:
            doc = yaml.safe_load(path.read_text())
            if schema:
                jsonschema.validate(doc, schema)
            projects.append(cls.parse(doc))
        return cls(projects)

    @staticmethod
    def parse(doc: dict) -> Project:
        return Project(
            name=doc["name"], workspace=doc["tracker"]["workspace"], identifier=doc["tracker"]["identifier"],
            tracker_name=doc["tracker"]["name"], repo=doc["repository"]["gitea"], base=doc["repository"]["base"],
            release=doc["repository"]["release"], domain=doc["domain"], previews=doc["previews"],
            checks=tuple(doc.get("checks", [])),
            people=tuple(Person(p["name"], frozenset(p.get("roles", [])), p.get("plane"), p.get("gitea"))
                         for p in doc.get("people", [])))

    def by_name(self, name: str) -> Project | None:
        return next((p for p in self.all if p.name == name), None)

    def by_repo(self, repo: str) -> Project | None:
        return next((p for p in self.all if p.repo == repo), None)

    def by_tracker(self, workspace: str, identifier: str) -> Project | None:
        return next((p for p in self.all if p.workspace == workspace and p.identifier == identifier), None)

    def of_event(self, event, identifier_of) -> Project | None:
        """The connected project an event belongs to; None for a tracker project or a repository
        that has no project file, whose events the loop leaves alone. identifier_of(workspace,
        project_id) names a tracker project (the tracker's webhooks carry only its id)."""
        if event.source == "gitea":
            return self.by_repo(event.data.get("repo", ""))
        if event.source == "plane":
            workspace = event.data.get("workspace") or ""
            identifier = identifier_of(workspace, event.data.get("project_id")) if workspace else None
            return self.by_tracker(workspace, identifier) if identifier else None
        return None


class LiveProjects:
    """Projects that follow their directory: in the cluster, the mounted ConfigMap target-projects,
    which the kubelet updates in place when a project file changes in git. A changed file is read
    on the next use; a file that does not load leaves the projects as they were."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._seen: tuple = ()
        self._projects = Projects.load(self.directory)
        self._seen = self._signature()
        log.info("projects: %s", ", ".join(p.name for p in self._projects.all) or "none")

    def _signature(self) -> tuple:
        paths = sorted({*self.directory.glob("*/project.yaml"), *self.directory.glob("*.yaml"),
                        *self.directory.glob("project.schema.json")})
        return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths)

    def current(self) -> Projects:
        with self._lock:
            seen = self._signature()
            if seen != self._seen:
                try:
                    self._projects = Projects.load(self.directory)
                    log.info("projects reloaded: %s", ", ".join(p.name for p in self._projects.all) or "none")
                except Exception:  # noqa: BLE001 - a broken file must not stop the loop
                    log.exception("the project files in %s do not load; the projects stay as they were", self.directory)
                self._seen = seen
            return self._projects

    def __getattr__(self, name):
        return getattr(self.current(), name)
