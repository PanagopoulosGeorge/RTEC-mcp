"""Typed vocabulary dataclasses — the machine-readable domain contract.

A ``Vocabulary`` is the closed set of symbols an LLM is allowed to emit for a
domain: input events, input fluents, background-knowledge predicates, named
threshold keys, value domains, and the known output (composite) fluents — each
output fluent tagged ``EDF`` / ``SDF`` (the central thesis split). Every event,
fluent, value, and threshold the generator produces must exist here; inventing a
plausible-but-absent predicate is the #1 documented failure mode (CLAUDE.md §2,
§5 invariant 3). The data is authored in ``domain/<name>.yaml`` and loaded by
``domain/loader.py``.

These types carry only declarative vocabulary — no business logic, no engine or
port dependencies (ARCHITECTURE.md §4: ``domain/*`` imports ``types`` only).
"""

from __future__ import annotations

from dataclasses import dataclass

from rtec_llm.types import FluentType


@dataclass(frozen=True, slots=True)
class Predicate:
    """An input-event signature: name, positional argument names, one-line meaning.

    ``arity`` is derived from ``arg_names`` by the loader, so the two can never
    drift out of sync.
    """

    name: str
    arg_names: tuple[str, ...]
    meaning: str

    @property
    def arity(self) -> int:
        return len(self.arg_names)


@dataclass(frozen=True, slots=True)
class BKPredicate:
    """A background-knowledge predicate the rules consult (facts or helpers).

    ``note`` records a semantic pitfall worth surfacing to the generator — most
    importantly that maritime ``inRange/3`` is *exclusive* (``>``, ``<``), the
    boundary-comparator divergence behind ``highSpeedNearCoast`` (CLAUDE.md §3).
    """

    name: str
    arg_names: tuple[str, ...]
    meaning: str
    note: str | None = None

    @property
    def arity(self) -> int:
        return len(self.arg_names)


@dataclass(frozen=True, slots=True)
class Fluent:
    """A fluent signature: name, argument names, the values it can take, regime.

    Used for both input fluents (e.g. ``proximity``) and output/composite
    fluents. ``fluent_type`` is the EDF/SDF tag carried onto every ``ScoreResult``
    so the evaluation harness can slice F1 by regime (ARCHITECTURE.md §2).
    """

    name: str
    arg_names: tuple[str, ...]
    values: tuple[str, ...]
    fluent_type: FluentType
    meaning: str

    @property
    def arity(self) -> int:
        return len(self.arg_names)


@dataclass(frozen=True, slots=True)
class ThresholdKey:
    """A named ``thresholds(Key, Value)`` parameter referenced by the rules.

    ``value`` is the canonical value from ``patternsParameters/thresholds.prolog``
    (stored as text — tuning it is one of the cheapest ways to shift recognised
    intervals, so it is reference data, not something this layer computes with).
    """

    key: str
    value: str
    meaning: str


@dataclass(frozen=True, slots=True)
class ValueDomain:
    """A closed enumeration of atoms (e.g. the six ``areaType`` values).

    These are the legal values for a fluent value or a typed argument; the
    validator treats them as closed sets when checking a fixture's symbols.
    """

    name: str
    atoms: tuple[str, ...]
    meaning: str


@dataclass(frozen=True, slots=True)
class EntityDomain:
    """How an entity argument is grounded for the RTEC ``grounding/1`` mechanism.

    Grounding declarations are derived *deterministically from a rule head*, never
    authored by the LLM (CLAUDE.md §5 invariant 4). For non-enumerated head
    arguments (the entities a fluent ranges over, e.g. a ``vessel``) the grounding
    body is a domain-membership goal over a dynamic domain. This record carries the
    knowledge the head-driven grounder needs:

    * ``predicate`` — the singular grounding predicate, declared elsewhere as a
      ``dynamicDomain`` (e.g. ``vessel`` ⇒ ``grounding(f(V)=...) :- vessel(V)``).
    * ``arg_names`` — the fluent/event argument names that denote this entity
      (e.g. ``vessel``, ``vessel1``, ``vessel2`` all denote a vessel).
    * ``pair_predicate`` — optional joint grounding predicate used when a head has
      exactly two of these entity arguments (e.g. ``vpair`` for vessel pairs); it
      grounds the observed pairs rather than the full cross-product.
    """

    predicate: str
    arg_names: tuple[str, ...]
    pair_predicate: str | None = None


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The complete, closed symbol set for one domain (e.g. ``msa``)."""

    name: str
    events: tuple[Predicate, ...]
    input_fluents: tuple[Fluent, ...]
    output_fluents: tuple[Fluent, ...]
    background_knowledge: tuple[BKPredicate, ...]
    thresholds: tuple[ThresholdKey, ...]
    value_domains: tuple[ValueDomain, ...]
    entities: tuple[EntityDomain, ...] = ()

    # -- name lookups (used by the generator's allow-list and the fixture validator) --

    def event_names(self) -> frozenset[str]:
        return frozenset(e.name for e in self.events)

    def output_fluent_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.output_fluents)

    def input_fluent_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.input_fluents)

    def fluent_names(self) -> frozenset[str]:
        """All fluent names — input and output."""
        return self.output_fluent_names() | self.input_fluent_names()

    def threshold_keys(self) -> frozenset[str]:
        return frozenset(t.key for t in self.thresholds)

    def predicate_functors(self) -> frozenset[str]:
        """Every functor the generator/fixtures may legally reference.

        Events, input + output fluents, background-knowledge predicates, the
        value-domain predicates (e.g. ``areaType``), and ``thresholds`` itself —
        the universe a fixture's ``domain_facts`` is validated against.
        """
        return (
            self.event_names()
            | self.fluent_names()
            | frozenset(p.name for p in self.background_knowledge)
            | frozenset(v.name for v in self.value_domains)
            | frozenset({"thresholds"})
        )

    def fluent(self, name: str) -> Fluent | None:
        """The input or output fluent named ``name``, or ``None`` if unknown."""
        for f in (*self.output_fluents, *self.input_fluents):
            if f.name == name:
                return f
        return None

    def value_domain(self, name: str) -> tuple[str, ...] | None:
        for v in self.value_domains:
            if v.name == name:
                return v.atoms
        return None

    def entity_for_arg(self, arg_name: str) -> EntityDomain | None:
        """The entity domain an argument named ``arg_name`` ranges over, if any."""
        for e in self.entities:
            if arg_name in e.arg_names:
                return e
        return None
