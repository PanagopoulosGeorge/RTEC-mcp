"""YAML → ``Vocabulary`` loader.

``load_domain("msa")`` reads ``domain/msa.yaml`` (authored from
``examples/maritime/`` — the authoritative, executable source) into the typed
``Vocabulary`` of :mod:`rtec_llm.domain.spec`.

Scalar coercion (the bare-``true`` trap): YAML parses an unquoted ``true`` /
``false`` / ``no`` into a Python ``bool``, which would corrupt Prolog atoms like
the fluent value ``true``. ``_atom`` coerces every scalar back to its Prolog
string form (``True`` → ``"true"``), so the YAML may quote atoms or not. Arity is
never read from the file — it is derived from each predicate's ``arg_names``, so
the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from rtec_llm.domain.spec import (
    BKPredicate,
    EntityDomain,
    Fluent,
    Predicate,
    ThresholdKey,
    ValueDomain,
    Vocabulary,
)
from rtec_llm.types import FluentType

_DOMAIN_DIR = Path(__file__).resolve().parent


def load_domain(name: str) -> Vocabulary:
    """Load and type the vocabulary for domain ``name`` (e.g. ``"msa"``, ``"har"``).

    Raises:
        FileNotFoundError: if ``domain/<name>.yaml`` does not exist.
        ValueError: if a required section or field is missing/malformed.
    """
    path = _DOMAIN_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"no domain spec for {name!r}: expected {path}. "
            f"Available: {sorted(p.stem for p in _DOMAIN_DIR.glob('*.yaml'))}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a YAML mapping at the top level")

    return Vocabulary(
        name=_atom(raw.get("name", name)),
        events=tuple(_event(e) for e in _section(raw, "events", path)),
        input_fluents=tuple(_fluent(f) for f in _section(raw, "input_fluents", path)),
        output_fluents=tuple(_fluent(f) for f in _section(raw, "output_fluents", path)),
        background_knowledge=tuple(_bk(b) for b in _section(raw, "background_knowledge", path)),
        thresholds=tuple(_threshold(t) for t in _section(raw, "thresholds", path)),
        value_domains=tuple(_value_domain(v) for v in _section(raw, "value_domains", path)),
        entities=tuple(_entity(e) for e in _section(raw, "entities", path)),
    )


def _section(raw: dict[str, Any], key: str, path: Path) -> list[Any]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path}: section {key!r} must be a list, got {type(value).__name__}")
    return value


def _event(item: dict[str, Any]) -> Predicate:
    return Predicate(
        name=_atom(item["name"]),
        arg_names=_atoms(item.get("arg_names", [])),
        meaning=str(item.get("meaning", "")),
    )


def _bk(item: dict[str, Any]) -> BKPredicate:
    note = item.get("note")
    return BKPredicate(
        name=_atom(item["name"]),
        arg_names=_atoms(item.get("arg_names", [])),
        meaning=str(item.get("meaning", "")),
        note=None if note is None else str(note),
    )


def _fluent(item: dict[str, Any]) -> Fluent:
    return Fluent(
        name=_atom(item["name"]),
        arg_names=_atoms(item.get("arg_names", [])),
        values=_atoms(item.get("values", [])),
        fluent_type=_fluent_type(item["fluent_type"]),
        meaning=str(item.get("meaning", "")),
    )


def _threshold(item: dict[str, Any]) -> ThresholdKey:
    return ThresholdKey(
        key=_atom(item["key"]),
        value=_atom(item["value"]),
        meaning=str(item.get("meaning", "")),
    )


def _value_domain(item: dict[str, Any]) -> ValueDomain:
    return ValueDomain(
        name=_atom(item["name"]),
        atoms=_atoms(item.get("atoms", [])),
        meaning=str(item.get("meaning", "")),
    )


def _entity(item: dict[str, Any]) -> EntityDomain:
    pair = item.get("pair_predicate")
    return EntityDomain(
        predicate=_atom(item["predicate"]),
        arg_names=_atoms(item.get("arg_names", [])),
        pair_predicate=None if pair is None else _atom(pair),
    )


def _fluent_type(value: Any) -> FluentType:
    text = _atom(value)
    if text not in ("EDF", "SDF"):
        raise ValueError(f"fluent_type must be 'EDF' or 'SDF', got {text!r}")
    return text  # type: ignore[return-value]  # narrowed by the guard above


def _atoms(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise ValueError(f"expected a list of atoms, got {type(values).__name__}")
    return tuple(_atom(v) for v in values)


def _atom(value: Any) -> str:
    """Coerce a YAML scalar to its Prolog atom string (dodging the bare-true trap)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
