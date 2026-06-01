"""Stateless rule generator.

One call = one rule. Given a target :class:`~rtec_llm.domain.spec.Fluent`, its
natural-language specification, and the domain :class:`Vocabulary`, the generator:

1. builds the system + user messages (:mod:`rtec_llm.generation.prompts`);
2. calls the model through :class:`~rtec_llm.ports.llm.LLMPort` (no provider
   detail leaks in here — only the port);
3. extracts the Prolog rule clauses robustly from the completion;
4. derives the grounding declaration deterministically from the rule head
   (:mod:`rtec_llm.generation.grounding`) — never from the LLM (CLAUDE.md §5
   invariant 4).

The generator is stateless and holds no memory across calls. It imports
``types``, ``domain``, ``ports/llm``, and its sibling generation modules only
(ARCHITECTURE.md §4): it neither executes nor scores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rtec_llm.domain.spec import Fluent, Vocabulary
from rtec_llm.generation import grounding, prompts
from rtec_llm.ports.llm import LLMPort
from rtec_llm.types import Message

_CODE_FENCE_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n(?P<body>.*?)```", re.DOTALL)
_CLAUSE_KEYWORD_RE = re.compile(r"\b(?:initiatedAt|terminatedAt|holdsFor|fi|p)\b")


@dataclass(frozen=True, slots=True)
class GeneratedRule:
    """The product of one generation call.

    Attributes:
        fluent_name: Target fluent the rule defines.
        rules: The extracted Prolog rule clauses (LLM-authored, declarations
            stripped).
        grounding: Deterministic grounding declaration for the rule head — derived
            from the vocabulary, not the LLM.
        raw_completion: The unmodified model output, logged per fluent for the
            thesis record.
        messages: The exact prompt sent, retained for reproducibility.
    """

    fluent_name: str
    rules: str
    grounding: str
    raw_completion: str
    messages: tuple[Message, ...]


def generate(
    *,
    fluent: Fluent,
    nl_spec: str,
    vocab: Vocabulary,
    llm: LLMPort,
    model: str,
    temperature: float,
) -> GeneratedRule:
    """Generate one RTEC rule for ``fluent`` from its NL spec (single shot)."""
    messages = prompts.build_messages(vocab=vocab, fluent=fluent, nl_spec=nl_spec)
    completion = llm.complete(messages=messages, model=model, temperature=temperature)
    rules = extract_prolog(completion)
    grounding_decl = grounding.grounding_for_rules(rules, vocab)
    # Always ground the declared target head, even if the model's head drifted
    # (a drifted head simply will not fire — an honest, scoreable outcome).
    target_grounding = grounding.grounding_for_fluent(fluent, vocab)
    if target_grounding not in grounding_decl:
        grounding_decl = f"{grounding_decl}\n{target_grounding}".strip()
    return GeneratedRule(
        fluent_name=fluent.name,
        rules=rules,
        grounding=grounding_decl,
        raw_completion=completion,
        messages=tuple(messages),
    )


def extract_prolog(completion: str) -> str:
    """Extract Prolog rule clauses from a model completion, robustly.

    Prefers fenced code blocks (concatenating every block, language tag ignored).
    Falls back to the raw text when no fence is present. In both cases declaration
    lines the model was told to omit (grounding/dynamicDomain/collectIntervals/…)
    are stripped, so a stray declaration never collides with the deterministic one.
    """
    blocks = [m.group("body") for m in _CODE_FENCE_RE.finditer(completion)]
    body = "\n".join(blocks) if blocks else completion
    return _strip_declarations(body).strip()


_DECLARATION_PREFIXES = (
    "grounding(",
    "dynamicDomain(",
    "collectIntervals(",
    "needsGrounding(",
    "buildFromPoints(",
    "index(",
)


def _strip_declarations(body: str) -> str:
    """Drop declaration clauses; grounding is generated deterministically elsewhere."""
    kept: list[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(prefix) for prefix in _DECLARATION_PREFIXES):
            continue
        kept.append(line)
    return "\n".join(kept)
