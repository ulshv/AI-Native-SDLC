"""The agent's answers in its own words (design decision 5): to a person's question about the item,
and to a comment it could not read as a request. One model call writes the reply from facts the
code hands it: the item's phase, ticket, pull request and run, the conversation so far, and what
this person may ask for now, which the policy says. A reply only informs; nothing happens
because of it, and what may happen is still the policy's.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from langchain_core.language_models.chat_models import BaseChatModel

from .model import Message

log = logging.getLogger(__name__)

# Writing, not classifying: a little freedom of wording, and still close to the facts.
TEMPERATURE = 0.3
MAX_LENGTH = 1500  # characters of a reply; a comment, not an essay
HISTORY = 8  # messages of the conversation the model sees


@dataclass(frozen=True)
class Facts:
    """What the reply may build on; everything else the model has to admit it does not know."""

    comment: str
    author: str
    project: str
    key: str = ""
    title: str = ""
    phase: str = ""
    pull_request: str = ""  # its address, when the item has one
    running: str = ""  # the mode of the agent run going on
    waiting_for: Sequence[str] = ()  # the questions the agent waits to have answered
    may_ask: Sequence[str] = ()  # what this person may ask @agent for now, in the policy's words
    conversation: Sequence[Message] = field(default_factory=tuple[Message, ...])
    unclear: bool = False  # the comment was not read as a question either


Reply = Callable[[Facts], str]

LOOP = """A ticket assigned to @agent goes: Drafting (the agent writes a spec) -> Spec review
(people approve it or ask for changes) -> Implementing (the agent writes the code in the same pull
request) -> Code review (people approve, ask for changes, or ask @agent to merge it into
development) -> On dev (merged into development) -> Released. Needs info: the agent waits for
answers. Closed: dropped. People act by writing to @agent in the ticket or the pull request, or by
setting the ticket's status."""

PROMPT = """You are @agent, an AI assistant that delivers software tickets in {project}.
{loop}

Facts about the work item (nothing else is known to you):
- ticket: {key} {title}
- phase: {phase}
- pull request: {pull_request}
- agent run going on: {running}
- questions the agent waits to have answered: {waiting_for}
- what {author} may ask you for now: {may_ask}

The conversation so far, oldest first:
{conversation}

{task}
Write in the language of the comment, plainly, in at most five sentences, without headings. Use
only the facts above; if they do not answer the question, say so and where to look (the ticket or
the pull request). Replying changes nothing: never say you will do or have done something. When
{author} seems to want something done, say how to ask for it, from what they may ask for now; if
it is not there, say it cannot be asked for in this phase.

{author}'s comment:
{comment}"""

ANSWER = "Answer the comment, which asks about the work item, the loop or what to do next."
CLARIFY = "You could not tell what the comment asks for. Say so briefly, and suggest what fits."


def _listed(values: Sequence[str]) -> str:
    return "; ".join(values) or "nothing"


def prompt(facts: Facts) -> str:
    conversation = "\n".join(f"{m.author}: {m.text[:500]}" for m in facts.conversation[-HISTORY:])
    return PROMPT.format(
        project=facts.project,
        loop=LOOP,
        key=facts.key or "(none yet)",
        title=facts.title,
        phase=facts.phase or "not in the loop",
        pull_request=facts.pull_request or "none yet",
        running=facts.running or "none",
        waiting_for=_listed(facts.waiting_for),
        author=facts.author,
        may_ask=_listed(facts.may_ask),
        conversation=conversation or "(empty)",
        task=CLARIFY if facts.unclear else ANSWER,
        comment=facts.comment,
    )


def writer(chat: BaseChatModel, fallback: str) -> Reply:
    """Replies through the model; an empty answer gets the fallback. A failed call fails the step,
    which is tried again, as any call to the outside."""

    def reply(facts: Facts) -> str:
        text = chat.invoke(prompt(facts)).text.strip()
        if not text:
            log.warning("the model wrote no reply to %r", facts.comment[:80])
            return fallback
        return text[:MAX_LENGTH]

    return reply
