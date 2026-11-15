"""Releases (design decision 15): the orchestrator prepares them itself, with no agent run, and a
person ships them.

What a release holds is what the base branch has and the release branch lacks; its tickets are
the keys in the merge commits Gitea writes ("Merge pull request 'EWA-12: ...' (#34) from
feat/EWA-12_slug into development"), as the product's release workflow reads them. Its version is
the next minor one after the latest v<major>.<minor>.<patch> tag, 0.1.0 when there is none; a
major version is set by hand.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def next_version(tags: Iterable[str]) -> str:
    versions = [tuple(int(n) for n in m.groups()) for tag in tags if (m := VERSION.match(tag))]
    if not versions:
        return "0.1.0"
    major, minor, _ = max(versions)
    return f"{major}.{minor + 1}.0"


def tickets(messages: Iterable[str], identifier: str) -> list[str]:
    """The project's ticket keys in the merge commits of feature branches, in number order."""
    merged = re.compile(rf"from feat/({re.escape(identifier)}-(\d+))_")
    keys = {(int(m[2]), m[1]) for message in messages if (m := merged.search(message.split("\n", 1)[0]))}
    return [key for _, key in sorted(keys)]


def title(version: str) -> str:
    return f"Release {version}"


def notes(version: str, keys: list[str], release: str) -> str:
    listed = "\n".join(f"- {key}" for key in keys) or "No tickets."
    return (
        f"{listed}\n\nPrepared by @agent. A person approves it and merges it into {release}; the "
        f"release workflow then tags v{version}, publishes the release, and the tickets go to Released."
    )
