"""Model provider factory for chat LLMs and embeddings.

Supports Azure OpenAI and OpenAI-compatible providers such as Qwen,
DeepSeek, Zhipu, and OpenAI by switching environment variables.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import (
    AzureChatOpenAI,
    AzureOpenAIEmbeddings,
    ChatOpenAI,
    OpenAIEmbeddings,
)
from pydantic import SecretStr

load_dotenv()


CHAT_PROVIDERS = {"openai", "qwen", "deepseek", "zhipu", "openai-compatible"}
EMBEDDING_PROVIDERS = {"openai", "qwen", "zhipu", "openai-compatible"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def get_model_provider() -> str:
    """Return configured provider, defaulting to Azure for backward compatibility."""
    return (_env("MODEL_PROVIDER", "azure") or "azure").strip().lower()


def create_chat_model(
    temperature: float = 0,
    max_tokens: int | None = None,
    extra_body: dict | None = None,
):
    """Create a chat model from environment configuration.

    Azure-compatible env vars:
        MODEL_PROVIDER=azure
        AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT,
        AZURE_OPENAI_VERSION

    OpenAI-compatible env vars:
        MODEL_PROVIDER=qwen|deepseek|zhipu|openai|openai-compatible
        LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    """
    provider = get_model_provider()

    if provider == "azure":
        return AzureChatOpenAI(
            azure_deployment=_env("AZURE_OPENAI_DEPLOYMENT"),
            api_version=_env("AZURE_OPENAI_VERSION"),
            temperature=temperature,
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
            timeout=float(_env("LLM_TIMEOUT_SECONDS", "60") or "60"),
            max_retries=int(_env("LLM_MAX_RETRIES", "2") or "2"),
            max_tokens=max_tokens,
            extra_body=extra_body,
        )

    if provider in CHAT_PROVIDERS:
        return ChatOpenAI(
            model=_env("LLM_MODEL", "qwen-plus") or "qwen-plus",
            api_key=SecretStr(_env("LLM_API_KEY", "") or ""),
            base_url=_env("LLM_BASE_URL"),
            temperature=temperature,
            timeout=float(_env("LLM_TIMEOUT_SECONDS", "60") or "60"),
            max_retries=int(_env("LLM_MAX_RETRIES", "2") or "2"),
            max_tokens=max_tokens,
            extra_body=extra_body,
        )

    raise ValueError(
        f"Unsupported MODEL_PROVIDER={provider!r}. "
        "Use azure, qwen, deepseek, zhipu, openai, or openai-compatible."
    )


def create_embedding_model():
    """Create an embedding model from environment configuration."""
    provider = (_env("EMBEDDING_PROVIDER") or get_model_provider()).strip().lower()

    if provider == "azure":
        return AzureOpenAIEmbeddings(
            azure_deployment=_env("AZURE_OPENAI_DEPLOYMENT_EMBEDDING"),
            api_key=SecretStr(_env("AZURE_OPENAI_API_KEY", "") or ""),
            api_version=_env("AZURE_OPENAI_EMBEDDING_VERSION", "2023-05-15"),
            azure_endpoint=_env("AZURE_OPENAI_ENDPOINT_EMBEDDING"),
            request_timeout=float(
                _env("EMBEDDING_TIMEOUT_SECONDS", "30") or "30"
            ),
            max_retries=int(_env("EMBEDDING_MAX_RETRIES", "2") or "2"),
        )

    if provider in EMBEDDING_PROVIDERS:
        return OpenAIEmbeddings(
            model=_env("EMBEDDING_MODEL", "text-embedding-v3") or "text-embedding-v3",
            api_key=SecretStr(_env("EMBEDDING_API_KEY") or _env("LLM_API_KEY", "") or ""),
            base_url=_env("EMBEDDING_BASE_URL") or _env("LLM_BASE_URL"),
            # OpenAI-compatible providers like DashScope (Qwen) only accept raw
            # strings; disable token-id batching to send plain text.
            check_embedding_ctx_length=False,
            request_timeout=float(
                _env("EMBEDDING_TIMEOUT_SECONDS", "30") or "30"
            ),
            max_retries=int(_env("EMBEDDING_MAX_RETRIES", "2") or "2"),
        )

    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER={provider!r}. "
        "Use azure, qwen, zhipu, openai, or openai-compatible."
    )
