"""Domain spec — typed vocabulary loader and validation.

Loads per-app YAML (``domain/<app>.yaml``) into typed ``Vocabulary`` objects.
Every event, fluent, value, and threshold the LLM emits must exist in this
spec (CLAUDE.md §5 invariant 3: never invent domain vocabulary).
"""

from rtec_llm.domain.loader import load_domain
from rtec_llm.domain.spec import (
    BKPredicate,
    EntityDomain,
    Fluent,
    Predicate,
    ThresholdKey,
    ValueDomain,
    Vocabulary,
)

__all__ = [
    "BKPredicate",
    "EntityDomain",
    "Fluent",
    "Predicate",
    "ThresholdKey",
    "ValueDomain",
    "Vocabulary",
    "load_domain",
]
