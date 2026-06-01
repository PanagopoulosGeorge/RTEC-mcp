"""LLM adapters — concrete ``LLMPort`` implementations and a provider factory.

Multi-provider comparison (GPT-4o / Claude / Gemini / Ollama / GLM) is an
explicit thesis experiment (CLAUDE.md §6). New providers are added *here* — at
the factory — not inline in the generator or CLI, which depend only on the
``LLMPort`` protocol.

OpenAI (GPT-4o) is wired first. The remaining providers are registered as clean
extension points: each raises a precise ``NotImplementedError`` naming the module
to add, so the wiring contract is visible without shipping unused dependencies.

Usage (composition root only)::

    from rtec_llm.adapters.llm import make_llm

    llm = make_llm("openai")  # reads OPENAI_API_KEY from the environment
    text = llm.complete(messages=..., model="gpt-4o", temperature=0.0)
"""

from __future__ import annotations

from collections.abc import Callable

from rtec_llm.adapters.llm.openai_provider import OpenAIProvider
from rtec_llm.ports.llm import LLMPort

#: Canonical provider identifiers (config strings) the factory understands.
PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini", "ollama", "glm")


def make_llm(provider: str, **kwargs: object) -> LLMPort:
    """Construct the ``LLMPort`` adapter for ``provider``.

    Args:
        provider: One of :data:`PROVIDERS`. The string is the config knob the CLI
            exposes (``--provider``).
        **kwargs: Provider-specific construction options forwarded to the adapter
            (e.g. ``api_key``, ``base_url``).

    Raises:
        ValueError: ``provider`` is not a known identifier.
        NotImplementedError: ``provider`` is reserved but its adapter is not wired
            yet (Claude/Gemini/Ollama/GLM land in a later phase — see CLAUDE.md §6).
    """
    factory = _FACTORIES.get(provider)
    if factory is None:
        raise ValueError(f"unknown LLM provider {provider!r}; known: {', '.join(PROVIDERS)}")
    return factory(**kwargs)


def _make_openai(**kwargs: object) -> LLMPort:
    return OpenAIProvider(**kwargs)  # type: ignore[arg-type]  # forwarded provider options


def _not_wired(name: str, module: str) -> Callable[..., LLMPort]:
    def factory(**_kwargs: object) -> LLMPort:
        raise NotImplementedError(
            f"the {name} provider is a reserved extension point; add its adapter at "
            f"rtec_llm/adapters/llm/{module} and register it in PROVIDERS/_FACTORIES "
            f"(CLAUDE.md §6: new providers are added at the factory)."
        )

    return factory


_FACTORIES: dict[str, Callable[..., LLMPort]] = {
    "openai": _make_openai,
    "anthropic": _not_wired("Anthropic Claude", "anthropic_provider.py"),
    "gemini": _not_wired("Google Gemini", "gemini_provider.py"),
    "ollama": _not_wired("Ollama (local)", "ollama_provider.py"),
    "glm": _not_wired("GLM", "glm_provider.py"),
}

__all__ = ["PROVIDERS", "OpenAIProvider", "make_llm"]
