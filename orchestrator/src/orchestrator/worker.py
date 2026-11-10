"""Workers: threads that take events from the queue and hand them to a handler."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .queue import Delivery, Queue

log = logging.getLogger(__name__)


class Workers:
    def __init__(self, queue: Queue, handler: Callable[[Delivery], None], count: int = 2, idle: float = 1.0):
        self.queue, self.handler, self.count, self.idle = queue, handler, count, idle
        self.stopping = threading.Event()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        for n in range(self.count):
            thread = threading.Thread(target=self.run, name=f"worker-{n}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                if not self.queue.process(self.handler):
                    self.stopping.wait(self.idle)
            except Exception:  # noqa: BLE001 - the database was away; try again shortly
                log.exception("the queue is not reachable")
                self.stopping.wait(5)

    def stop(self) -> None:
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=10)
