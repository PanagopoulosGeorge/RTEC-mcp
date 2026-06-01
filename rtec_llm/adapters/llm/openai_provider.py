"""``LLMPort`` adapter for OpenAI chat-completions (GPT-4o primary).

The first concrete provider per CLAUDE.md §6: OpenAI is the primary model for the
single-shot baseline. The ``openai`` SDK is imported lazily inside
:meth:`OpenAIProvider.complete` so the package imports without it — only this
adapter (when actually used) requires ``pip install rtec-llm[openai]``.

Statelessness and error policy follow :class:`rtec_llm.ports.llm.LLMPort`: no
turns are retained between calls, and provider errors (auth, rate-limit,
transport) propagate as exceptions rather than being swallowed.
"""

from __future__ import annotations

import os
from typing import Any

from rtec_llm.types import Message


class OpenAIProvider:
    """OpenAI-backed :class:`rtec_llm.ports.llm.LLMPort`.

    Args:
        api_key: OpenAI API key. Defaults to the ``OPENAI_API_KEY`` environment
            variable. Resolved lazily — construction never fails on a missing key;
            only :meth:`complete` does, and only when actually called.
        base_url: Optional API base URL override (e.g. an Azure/OpenAI-compatible
            gateway). Defaults to the SDK default.
        organization: Optional OpenAI organization id.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url
        self._organization = organization
        self._client: Any | None = None

    def complete(self, *, messages: list[Message], model: str, temperature: float) -> str:
        """Return the assistant completion for ``messages``; see ``LLMPort``."""
        client = self._ensure_client()
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content:
            raise RuntimeError(f"OpenAI returned an empty completion for model {model!r}")
        return content

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it or pass api_key=... (see .env.example)."
            )
        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - import-time guard
            raise RuntimeError(
                "The 'openai' package is required for the OpenAI provider. "
                "Install it with: pip install 'rtec-llm[openai]'"
            ) from exc
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            organization=self._organization,
        )
        return self._client
