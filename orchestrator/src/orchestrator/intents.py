"""Reading what a comment asks for (design decision 5): one model call, answered in a fixed
shape. The model only names the intent; policy.decide() says whether it may happen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from .policy import READABLE, Intent

log = logging.getLogger(__name__)


# A reading is one short call: a minute is plenty, and the provider's client retries what failed
# before an answer twice, with backoff.
MODEL_TIMEOUT, MODEL_RETRIES = 60.0, 2


def chat_model(model: str, base_url: str, api_key: str, **kwargs: Any) -> BaseChatModel:
    """The client for a model, by its prefix: DeepSeek's own client keeps its reasoning blocks,
    which the OpenAI client drops and which multi-turn calls with tools need. Both talk to the
    configured OpenAI-compatible base URL."""
    common = {
        "model": model,
        "api_key": api_key,
        "temperature": 0,
        "timeout": MODEL_TIMEOUT,
        "max_retries": MODEL_RETRIES,
        **kwargs,
    }
    if model.startswith("deepseek/"):
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(api_base=base_url, **common)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(base_url=base_url, **common)


class ModelReading(BaseModel):
    """The shape the model answers in. The intent stays a string here: a word outside the list
    reads as none instead of failing the call."""

    intent: str = Field(description="One of: " + ", ".join(READABLE))
    confidence: float = Field(ge=0, le=1, description="How sure you are, from 0 to 1")


PROMPT = """You read comments people write to @agent, an AI assistant that delivers software
tickets: it writes a spec, then the code, in a pull request; people approve and merge.

Name the one thing the comment asks @agent to do:
- approve: the spec or the code is fine, go on ("looks good", "ok", "go ahead", "approved")
- request_changes: change the spec or the code, with what to change
- reject: drop the ticket, stop the work ("won't do", "close it")
- answer: answers to questions @agent asked
- merge: merge the pull request into development ("merge it", "ship it to dev")
- archive: create a pull request that archives the change's spec ("create archive PR")
- prepare_release: prepare a release of what is merged ("let's release", "prepare a release")
- none: anything else, or unclear

The work item is in the phase "{phase}".
@agent's last message there: {last}

The comment:
{comment}"""


@dataclass(frozen=True)
class Reading:
    intent: Intent
    confidence: float


NOTHING = Reading(Intent.NONE, 0.0)


def read(chat: BaseChatModel, comment: str, phase: str, last_message: str = "") -> Reading:
    """The intent, or none when the model's answer does not have the shape asked for (prose, an
    out-of-range confidence): the agent then asks what is meant, instead of failing the event."""
    structured = chat.with_structured_output(ModelReading, method="function_calling")
    try:
        reading = structured.invoke(PROMPT.format(phase=phase, last=last_message or "(none)", comment=comment))
    except ValueError as error:  # pydantic's ValidationError and the parser's errors are ValueErrors
        log.warning("the model's reading of %r has no usable shape: %s", comment[:80], error)
        return NOTHING
    if not isinstance(reading, ModelReading):
        log.warning("the model gave no reading of %r", comment[:80])
        return NOTHING
    intent = Intent(reading.intent) if reading.intent in READABLE else Intent.NONE
    log.info("read %r as %s (%.2f)", comment[:80], intent, reading.confidence)
    return Reading(intent, reading.confidence)


def clear(reading: Reading, threshold: float) -> bool:
    """A reading the loop may act on: below the threshold, or none, the agent asks instead."""
    return reading.intent is not Intent.NONE and reading.confidence >= threshold
