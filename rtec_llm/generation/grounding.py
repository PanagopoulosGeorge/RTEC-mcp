"""Deterministic grounding generation from a rule head.

CLAUDE.md §5 invariant 4: grounding declarations are derived programmatically
from the head shape, never authored by the LLM. Missing grounding is the #1
silent failure mode in RTEC rules (CLAUDE.md §3) — generating it ourselves
removes that whole failure class.

An RTEC ``grounding/1`` clause maps a (possibly partially instantiated) fluent or
event head to the domain-membership goals that enumerate its legal groundings,
e.g.::

    grounding(withinArea(Vessel, AreaType)=true) :- vessel(Vessel), areaType(AreaType).
    grounding(rendezVous(Vessel1, Vessel2)=true) :- vpair(Vessel1, Vessel2).
    grounding(gap(Vessel)=PortStatus)            :- vessel(Vessel), portStatus(PortStatus).

This module derives those goals from a :class:`~rtec_llm.domain.spec.Fluent`'s
declared head shape (its ``arg_names`` and ``values``) and the domain's
:class:`~rtec_llm.domain.spec.EntityDomain` / :class:`ValueDomain` metadata:

* an argument named after a value domain (``areaType``) grounds over that
  domain's 1-ary predicate;
* an argument naming an entity (``vessel``/``vessel1``/``vessel2``) grounds over
  the entity predicate (``vessel/1``), or — when a head carries exactly two
  entity arguments and the domain declares a ``pair_predicate`` — over the pair
  predicate (``vpair/2``), matching the observed-pairs grounding the hand-written
  maritime rules use;
* a non-boolean value grounds over the value domain whose atoms cover it;
* a boolean (``true``) value is a literal, contributing no goal.

It imports ``types`` and ``domain`` only (ARCHITECTURE.md §4); it never touches
ports, fixtures, or an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rtec_llm.domain.spec import Fluent, Vocabulary

_HEAD_KEYWORDS = ("initiatedAt", "terminatedAt", "holdsFor")
# A fluent term ``name(arg1, arg2, ...)=value`` or ``name=value``.
_FLUENT_TERM_RE = re.compile(
    r"^(?P<name>[a-z]\w*)\s*(?:\(\s*(?P<args>.*?)\s*\))?\s*=\s*(?P<value>.+)$",
    re.DOTALL,
)


_RULE_KEYWORDS = ("initiatedAt", "terminatedAt", "holdsFor", "fi", "p")
# Fluent functors referenced in a clause body (holdsFor/holdsAt/start/end of F=V).
_REFERENCE_RE = re.compile(r"\b(?:holdsFor|holdsAt|start|end)\(\s*([a-z]\w*)\s*\(")


@dataclass(frozen=True, slots=True)
class ParsedHead:
    """The fluent identity parsed from a generated rule clause head."""

    name: str
    arity: int
    value: str


def parse_heads(rules: str) -> tuple[ParsedHead, ...]:
    """Parse every ``initiatedAt``/``terminatedAt``/``holdsFor`` head in ``rules``.

    Deduplicated, order-preserving. Used to discover which fluents a generated (or
    scaffolded) rule set defines, so each can be grounded from its head.
    """
    seen: dict[tuple[str, int, str], ParsedHead] = {}
    for clause in _split_clauses(rules):
        head = _clause_head(clause)
        if head is None:
            continue
        key = (head.name, head.arity, head.value)
        seen.setdefault(key, head)
    return tuple(seen.values())


def grounding_for_fluent(fluent: Fluent, vocab: Vocabulary) -> str:
    """Return the ``grounding/1`` clause(s) for ``fluent``, derived from its head.

    One clause per declared value (so a multi-valued fluent like ``gap`` whose
    value ranges over a value domain still produces a single value-variable
    clause). The output is deterministic and independent of the LLM.
    """
    head_args, arg_goals = _argument_grounding(fluent.arg_names, vocab)
    value_term, value_goal = _value_grounding(fluent.values, vocab)
    goals = arg_goals + value_goal

    head = fluent.name if not head_args else f"{fluent.name}({', '.join(head_args)})"
    head = f"{head}={value_term}"
    if goals:
        return f"grounding({head}) :- {', '.join(goals)}."
    return f"grounding({head})."


def grounding_for_rules(rules: str, vocab: Vocabulary) -> str:
    """Generate grounding declarations for every known fluent defined in ``rules``.

    Parses the heads, resolves each against the vocabulary, and emits the
    deterministic grounding clause for it. Heads whose functor is not a known
    fluent are skipped (a vocabulary violation handled upstream); they cannot be
    grounded without a declared head shape.
    """
    clauses: list[str] = []
    emitted: set[str] = set()
    for head in parse_heads(rules):
        fluent = vocab.fluent(head.name)
        if fluent is None:
            continue
        clause = grounding_for_fluent(fluent, vocab)
        if clause not in emitted:
            emitted.add(clause)
            clauses.append(clause)
    return "\n".join(clauses)


# ---------------------------------------------------------------------------
# Clause-level text utilities (pure; reused by the composition root for
# scaffolding prerequisite rules from a reference event description)
# ---------------------------------------------------------------------------


def split_clauses(text: str) -> list[str]:
    """Split Prolog source into clauses on top-level ``.`` terminators (no trailing dot)."""
    return _split_clauses(text)


def clause_fluent(clause: str) -> str | None:
    """The fluent functor a rule clause defines, or ``None`` if not a rule clause.

    Handles ``initiatedAt``/``terminatedAt``/``holdsFor`` (head fluent) and the
    ``fi``/``p`` interval-completion declarations whose first argument is a fluent.
    """
    head_split = _top_level_neck(clause)
    head_text = (clause[:head_split] if head_split is not None else clause).strip()
    for keyword in _RULE_KEYWORDS:
        if head_text.startswith(keyword + "("):
            inner = _balanced_inside(head_text, len(keyword))
            if not inner:
                return None
            term = _split_args(inner)[0]
            parsed = _parse_term(term)
            if parsed is not None:
                return parsed.name
            match = re.match(r"\s*([a-z]\w*)", term)
            return match.group(1) if match else None
    return None


def grounded_functor(clause: str) -> str | None:
    """For a ``grounding(...)`` clause, the functor being grounded; else ``None``."""
    head_split = _top_level_neck(clause)
    head_text = (clause[:head_split] if head_split is not None else clause).strip()
    if not head_text.startswith("grounding("):
        return None
    inner = _balanced_inside(head_text, len("grounding"))
    if not inner:
        return None
    match = re.match(r"\s*([a-z]\w*)", inner)
    return match.group(1) if match else None


def referenced_functors(clause: str) -> set[str]:
    """Fluent functors referenced via holdsFor/holdsAt/start/end in ``clause``."""
    return set(_REFERENCE_RE.findall(clause))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _clause_head(clause: str) -> ParsedHead | None:
    """Parse the fluent head of one ``initiatedAt``/``terminatedAt``/``holdsFor`` clause."""
    body_split = _top_level_neck(clause)
    head_text = clause[:body_split] if body_split is not None else clause
    head_text = head_text.strip()
    for keyword in _HEAD_KEYWORDS:
        prefix = keyword + "("
        if head_text.startswith(prefix):
            inner = _balanced_inside(head_text, len(keyword))
            if inner is None:
                return None
            first_arg = _split_args(inner)[0] if inner.strip() else ""
            return _parse_term(first_arg)
    return None


def _parse_term(term: str) -> ParsedHead | None:
    match = _FLUENT_TERM_RE.match(term.strip())
    if match is None:
        return None
    args = match.group("args")
    arity = 0 if args is None or not args.strip() else len(_split_args(args))
    return ParsedHead(name=match.group("name"), arity=arity, value=match.group("value").strip())


def _split_clauses(text: str) -> list[str]:
    """Split Prolog source into clauses on top-level ``.`` terminators.

    Strips line comments first, then tracks parenthesis/bracket depth and quoting
    so a ``.`` inside a term or string never ends a clause.
    """
    clauses: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for line in text.splitlines():
        line = _strip_comment(line)
        for i, ch in enumerate(line):
            if quote is not None:
                current.append(ch)
                if ch == quote and not _escaped(line, i):
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                current.append(ch)
                continue
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            if ch == "." and depth == 0:
                clause = "".join(current).strip()
                if clause:
                    clauses.append(clause)
                current = []
                continue
            current.append(ch)
        current.append("\n")
    tail = "".join(current).strip()
    if tail:
        clauses.append(tail)
    return clauses


def _strip_comment(line: str) -> str:
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote and not _escaped(line, i):
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "%":
            return line[:i]
    return line


def _escaped(line: str, index: int) -> bool:
    backslashes = 0
    j = index - 1
    while j >= 0 and line[j] == "\\":
        backslashes += 1
        j -= 1
    return backslashes % 2 == 1


def _top_level_neck(clause: str) -> int | None:
    """Index of the top-level ``:-`` neck in ``clause``, or ``None`` for a fact."""
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(clause):
        if quote is not None:
            if ch == quote and not _escaped(clause, i):
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == ":" and depth == 0 and clause[i + 1 : i + 2] == "-":
            return i
    return None


def _balanced_inside(text: str, open_index: int) -> str | None:
    """Return the content between the parenthesis at/after ``open_index`` and its match."""
    start = text.find("(", open_index)
    if start == -1:
        return None
    depth = 0
    quote: str | None = None
    for i in range(start, len(text)):
        ch = text[i]
        if quote is not None:
            if ch == quote and not _escaped(text, i):
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    return None


def _split_args(args: str) -> list[str]:
    """Split a comma-separated argument list, respecting nested parens/brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in args:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _argument_grounding(
    arg_names: tuple[str, ...], vocab: Vocabulary
) -> tuple[list[str], list[str]]:
    """Map each head argument to a fresh variable and its domain-membership goal."""
    variables = [_variable_for(name) for name in arg_names]
    goals: list[str] = []

    # Collapse two same-entity arguments onto a pair predicate when one is declared
    # (vpair) — the hand-written maritime rules ground vessel pairs this way.
    entity_positions = [
        i for i, name in enumerate(arg_names) if vocab.entity_for_arg(name) is not None
    ]
    pair_handled = False
    if len(entity_positions) == 2:
        i, j = entity_positions
        entity = vocab.entity_for_arg(arg_names[i])
        if entity is not None and entity.pair_predicate is not None:
            goals.append(f"{entity.pair_predicate}({variables[i]}, {variables[j]})")
            pair_handled = True

    for index, name in enumerate(arg_names):
        if vocab.value_domain(name) is not None:
            goals.append(f"{name}({variables[index]})")
            continue
        entity = vocab.entity_for_arg(name)
        if entity is not None:
            if pair_handled and index in entity_positions:
                continue
            goals.append(f"{entity.predicate}({variables[index]})")

    # Reorder so the (joint) entity goals lead, matching the conventional layout.
    return variables, goals


def _value_grounding(values: tuple[str, ...], vocab: Vocabulary) -> tuple[str, list[str]]:
    """Return the head value term and any goal needed to ground a value variable."""
    if values == ("true",) or values == ("false",) or set(values) <= {"true", "false"}:
        # A boolean fluent: the value is a literal in the head, no grounding goal.
        # (Boolean fluents in this codebase are uniformly ``=true``.)
        return values[0], []
    domain_name = _value_domain_covering(values, vocab)
    if domain_name is None:
        # Unknown value set — fall back to the first declared value as a literal.
        return values[0], []
    variable = _variable_for(domain_name)
    return variable, [f"{domain_name}({variable})"]


def _value_domain_covering(values: tuple[str, ...], vocab: Vocabulary) -> str | None:
    """The smallest value domain whose atoms cover every declared value, if any."""
    wanted = set(values)
    best: tuple[int, str] | None = None
    for domain in vocab.value_domains:
        atoms = set(domain.atoms)
        if wanted <= atoms and (best is None or len(atoms) < best[0]):
            best = (len(atoms), domain.name)
    return None if best is None else best[1]


def _variable_for(name: str) -> str:
    """Turn an argument/domain name into a Prolog variable (``vessel1`` → ``Vessel1``)."""
    cleaned = re.sub(r"\W", "_", name)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else "X"
