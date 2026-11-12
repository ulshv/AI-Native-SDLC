"""The model provider's clients: any OpenAI-compatible API (RouterAI by default), a client per
model chosen by the model's prefix. What a call is for, and so its temperature, is the caller's.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

# A call answers within a minute, and the provider's client retries what failed before an answer
# twice, with backoff.
TIMEOUT, RETRIES = 60.0, 2


def chat_model(model: str, base_url: str, api_key: str, temperature: float) -> BaseChatModel:
    """The client for a model, by its prefix: DeepSeek's own client keeps its reasoning blocks,
    which the OpenAI client drops and which multi-turn calls with tools need. Both talk to the
    configured OpenAI-compatible base URL."""
    if model.startswith("deepseek/"):
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(
            model=model,
            base_url=base_url,
            api_key=SecretStr(api_key),
            temperature=temperature,
            timeout=TIMEOUT,
            max_retries=RETRIES,
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
        temperature=temperature,
        timeout=TIMEOUT,
        max_retries=RETRIES,
    )
