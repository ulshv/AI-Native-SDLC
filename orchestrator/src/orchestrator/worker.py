"""Workers: threads that take events from the queue and hand them to a handler.

Stopping (on SIGTERM, when uvicorn ends) takes no more events and waits for the ones being
handled; what is still being handled after the grace is released, so another pod takes it at
once instead of after its lease. Its handler may still finish here: effects are at least once.
"""

from __future__ import annotations

import logging
import threading
import time

from .queue import Handler, Queue

log = logging.getLogger(__name__)

GRACE_SECONDS = 45.0  # within the pod's terminationGracePeriodSeconds


class Workers:
    def __init__(self, queue: Queue, handler: Handler, count: int = 2, idle: float = 1.0):
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
            except Exception:
                log.exception("the queue is not reachable")
                self.stopping.wait(5)

    def stop(self, grace: float = GRACE_SECONDS) -> None:
        self.stopping.set()
        deadline = time.monotonic() + grace
        for thread in self.threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        released = self.queue.release_unfinished() if any(t.is_alive() for t in self.threads) else 0
        log.info("workers stopped, %d events released", released)
