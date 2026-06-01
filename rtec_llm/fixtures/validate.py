"""Fixture ↔ vocabulary symbol validation.

The guard against the #1 documented failure mode (CLAUDE.md §2, §5 invariant 3):
a fixture must reference only symbols that exist in its domain ``Vocabulary``.
``unknown_symbols`` returns a human-readable list of every violation; an empty
list means the fixture is fully grounded.

Checked:
* ``fluent_name`` is a known fluent, and its ``fluent_type`` matches the
  vocabulary's EDF/SDF tag;
* every ``prerequisite_fluents`` entry is a known fluent;
* every predicate functor in ``domain_facts`` is a known vocabulary predicate,
  and every ``thresholds(Key, _)`` key is a known threshold key.
"""

from __future__ import annotations

import re

from rtec_llm.domain.spec import Vocabulary
from rtec_llm.fixtures.schema import FixtureSpec

_FUNCTOR_RE = re.compile(r"([a-z]\w*)\s*\(")
_THRESHOLD_KEY_RE = re.compile(r"thresholds\(\s*([a-zA-Z]\w*)\s*,")


def unknown_symbols(spec: FixtureSpec, vocab: Vocabulary) -> list[str]:
    """Return every symbol the fixture references that is absent from ``vocab``."""
    problems: list[str] = []
    fluents = vocab.fluent_names()

    if spec.fluent_name not in fluents:
        problems.append(
            f"fluent_name {spec.fluent_name!r} is not a known fluent in domain {vocab.name!r}"
        )
    else:
        known = vocab.fluent(spec.fluent_name)
        if known is not None and known.fluent_type != spec.fluent_type:
            problems.append(
                f"fluent_type {spec.fluent_type!r} for {spec.fluent_name!r} disagrees with "
                f"the vocabulary tag ({known.fluent_type})"
            )

    for pre in spec.prerequisite_fluents:
        if pre not in fluents:
            problems.append(
                f"prerequisite_fluent {pre!r} is not a known fluent in domain {vocab.name!r}"
            )

    functors = vocab.predicate_functors()
    threshold_keys = vocab.threshold_keys()
    for fact in spec.domain_facts:
        for functor in _FUNCTOR_RE.findall(fact):
            if functor not in functors:
                problems.append(f"domain_fact references unknown predicate {functor!r}: {fact!r}")
        for key in _THRESHOLD_KEY_RE.findall(fact):
            if key not in threshold_keys:
                problems.append(f"domain_fact references unknown threshold key {key!r}: {fact!r}")

    return problems
