"""Rule generation — stateless LLM call from spec + feedback to Prolog rule.

The generator holds no memory across iterations; its only inputs are the
prompt and the current repair feedback. Grounding is generated deterministically
from the rule head, NOT by the LLM (CLAUDE.md §5 invariant 4).
"""

from rtec_llm.generation.generator import GeneratedRule, extract_prolog, generate
from rtec_llm.generation.grounding import (
    ParsedHead,
    clause_fluent,
    grounded_functor,
    grounding_for_fluent,
    grounding_for_rules,
    parse_heads,
    referenced_functors,
    split_clauses,
)
from rtec_llm.generation.prompts import build_messages, render_vocabulary

__all__ = [
    "GeneratedRule",
    "ParsedHead",
    "build_messages",
    "clause_fluent",
    "extract_prolog",
    "generate",
    "grounded_functor",
    "grounding_for_fluent",
    "grounding_for_rules",
    "parse_heads",
    "referenced_functors",
    "render_vocabulary",
    "split_clauses",
]
