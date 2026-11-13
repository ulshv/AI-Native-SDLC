"""A value read from files and read again when they change: in the cluster, a mounted ConfigMap or
Secret, which the kubelet updates in place when its source changes. So a project file pushed to
git, or a person Plane's setup Job added, reaches the orchestrator without a restart.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

CHECK_EVERY = 5.0  # seconds between looks at the files


class Live[T]:
    """The value is read once at start, which must succeed. Later the files are looked at every
    CHECK_EVERY seconds at most, and the value is read again when one changed; a reading that
    fails, or files the kubelet swaps while they are listed, leave the value as it was."""

    def __init__(
        self,
        what: str,
        files: Callable[[], list[Path]],
        read: Callable[[], T],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.what, self.files, self.read, self.clock = what, files, read, clock
        self._lock = threading.Lock()
        self._value = read()
        self._seen, self._checked = self._signature(), clock()

    def _signature(self) -> tuple[tuple[str, int, int], ...]:
        return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(self.files()))

    def get(self) -> T:
        with self._lock:
            if self.clock() - self._checked >= CHECK_EVERY:
                self._checked = self.clock()
                self._read_if_changed()
            return self._value

    def _read_if_changed(self) -> None:
        try:
            seen = self._signature()
        except OSError:  # swapped as they are listed: look again next time
            return
        if seen == self._seen:
            return
        self._seen = seen
        try:
            self._value = self.read()
            log.info("%s read again", self.what)
        except Exception:
            log.exception("%s do not read; they stay as they were", self.what)
