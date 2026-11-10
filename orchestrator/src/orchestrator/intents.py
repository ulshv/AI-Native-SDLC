"""Reading what a comment asks for (design decision 5): one model call, answered in a fixed
shape. The model only names the intent; policy.decide() says whether it may happen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from .policy import INTENTS

log = logging.getLogger(__name__)


def chat_model(model: str, base_url: str, api_key: str, **kwargs) -> BaseChatModel:
    """The client for a model, by its prefix: DeepSeek's own client keeps its reasoning blocks,
    which the OpenAI client drops and which multi-turn calls with tools need. Both talk to the
    configured OpenAI-compatible base URL."""
    if model.startswith("deepseek/"):
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(model=model, api_base=base_url, api_key=api_key, temperature=0, **kwargs)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, temperature=0, **kwargs)


class Reading(BaseModel):
    intent: str = Field(description="One of: " + ", ".join(INTENTS))
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
class Intent:
    name: str
    confidence: float


def read(chat: BaseChatModel, comment: str, phase: str, last_message: str = "") -> Intent:
    """The intent, or none when the model's answer does not have the shape asked for (prose, an
    out-of-range confidence): the agent then asks what is meant, instead of failing the event."""
    structured = chat.with_structured_output(Reading, method="function_calling")
    try:
        reading = structured.invoke(PROMPT.format(phase=phase, last=last_message or "(none)", comment=comment))
    except ValueError as error:  # pydantic's ValidationError and the parser's errors are ValueErrors
        log.warning("the model's reading of %r has no usable shape: %s", comment[:80], error)
        return Intent("none", 0.0)
    if not isinstance(reading, Reading):
        log.warning("the model gave no reading of %r", comment[:80])
        return Intent("none", 0.0)
    name = reading.intent if reading.intent in INTENTS else "none"
    log.info("read %r as %s (%.2f)", comment[:80], name, reading.confidence)
    return Intent(name, reading.confidence)


def clear(intent: Intent, threshold: float) -> bool:
    """A reading the loop may act on: below the threshold, or none, the agent asks instead."""
    return intent.name != "none" and intent.confidence >= threshold
