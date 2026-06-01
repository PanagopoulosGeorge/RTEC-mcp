"""Typed fixture spec — the behavioural-annotation input contract.

One ``FixtureSpec`` is one test case: an NL description of a target fluent plus
everything needed to score a generated rule for it against the engine. The
behavioural annotation — not a reference Prolog rule — is what the generator
sees (CLAUDE.md §6; do not reintroduce the abandoned "Approach 1").

Ground truth is a path to a raw RTEC ``recognitions(...)`` file (so P3's
``parser.parse_file`` consumes it directly) using RTEC's half-open ``[s, e)``
convention; a drift there is invisible at parse time and silently inflates
FN/FP across every iteration (ARCHITECTURE.md §1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rtec_llm.types import FluentType, Window


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """One annotation-driven fixture.

    Attributes:
        domain: Name of the domain vocabulary this fixture is graded against
            (e.g. ``"msa"``) — resolved via ``domain.load_domain`` and used by
            the symbol validator (``fixtures.validate``).
        fluent_name: Name of the target composite fluent (e.g. ``"loitering"``).
            Must be a known output (or input) fluent in the domain ``Vocabulary``.
        fluent_type: EDF or SDF tag, carried through to every ``ScoreResult`` so
            the evaluation harness can slice F1 by regime. Must match the
            vocabulary's tag for ``fluent_name``.
        nl_spec: Behavioural annotation — the NL description of what the fluent
            means. The generator's only semantic input.
        event_stream_ref: Path to the input CSV (resolved to an absolute path by
            the loader).
        ground_truth_file: Path to a raw RTEC ``recognitions(...)`` file of the
            reference intervals for ``fluent_name`` over ``window``. MUST use
            RTEC's half-open ``[s, e)`` convention. Consumed by the scoring
            adapter's ``parser.parse_file`` — never re-parsed elsewhere.
        window: The RTEC window the ground truth was derived over. The repair
            loop MUST run the engine over this exact window, or the scored
            intervals will not align with the ground truth.
        domain_facts: Optional Prolog facts the fixture asserts on top of the
            loaded domain spec (e.g. a one-off ``thresholds(...)`` override
            scoped to this test case). Every predicate referenced must exist in
            the domain ``Vocabulary``.
        prerequisite_fluents: Names of other fluents that must already exist in
            the rule set before the generator is asked to write this one. Each
            must be a known fluent in the ``Vocabulary``; the repair loop
            resolves prerequisites in order.
    """

    domain: str
    fluent_name: str
    fluent_type: FluentType
    nl_spec: str
    event_stream_ref: Path
    ground_truth_file: Path
    window: Window
    domain_facts: tuple[str, ...] = field(default_factory=tuple)
    prerequisite_fluents: tuple[str, ...] = field(default_factory=tuple)
