"""LLMPort — abstract chat-completion provider.

The generator and orchestrator depend on this port; they never import a
concrete adapter or touch an HTTP client directly.

Multi-provider comparison (GPT-4o / Claude / Gemini / Ollama / GLM) is an
explicit thesis experiment (CLAUDE.md §6). The concrete adapters and
selection factory land in P5; the port is defined now so the generator,
prompts, and orchestrator can be wired against a stable interface from the
first commit.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rtec_llm.types import Message


@runtime_checkable
class LLMPort(Protocol):
    """Single-shot chat-completion over a typed message list.

    Implementations MUST:

    * Be stateless. The repair loop tracks history via ``RepairState`` and
      passes the full message list on each call — the adapter does not retain
      prior turns.
    * Raise on provider errors (auth, rate-limit, transport). Unlike
      ``EnginePort``, LLM failures are not diagnostic signal for the repair
      loop; they are infrastructure issues the caller decides how to react to.
    * Treat ``model`` as opaque. The factory in
      ``rtec_llm/adapters/llm/`` (P5) is what enforces the provider/model
      mapping; the port does not validate the string.
    """

    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        temperature: float,
    ) -> str:
        """Return the assistant's text completion for the given message list.

        Args:
            messages: Chat-style message list. Typically a system prompt
                followed by a user prompt, with optional interleaved assistant
                turns for few-shot or repair context.
            model: Provider-specific model identifier
                (e.g. ``gpt-4o``, ``claude-opus-4-7``, ``gemini-2.5-pro``,
                ``llama3.1:70b``). Validated by the adapter factory, not here.
            temperature: Sampling temperature. The repair loop typically uses
                a low non-zero value (≈0.2) to allow iteration diversity
                without catastrophic drift; the single-shot baseline uses 0.

        Returns:
            The assistant's text completion. No tool calls, no streaming,
            no structured output — those are deferred to a later phase if
            the generator ever needs them.
        """
        ...
