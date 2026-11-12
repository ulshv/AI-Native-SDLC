"""Logs: one JSON line per record, through structlog; `| jq` reads them in a terminal. Every
module logs through the standard logging module; what is bound with `bound` (a delivery, a
thread, a run) goes on each line logged meanwhile, in the same thread, so one item can be
followed across workers."""

from __future__ import annotations

import logging

import structlog
from structlog.contextvars import bound_contextvars as bound

__all__ = ["bound", "configure"]


def configure(level: int = logging.INFO) -> None:
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
