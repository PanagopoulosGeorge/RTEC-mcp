"""System + user prompt builders for rule generation.

The system prompt is a compact RTEC primer (the EDF/SDF regimes and the core
predicates) followed by the *loaded* :class:`~rtec_llm.domain.spec.Vocabulary`
rendered as a closed allow-list — the LLM may only use symbols that appear here
(CLAUDE.md §2, §5 invariant 3). The user prompt is the fixture's natural-language
specification of the single target fluent, plus the exact head signature the rule
must define so its output can be executed and scored.

Grounding is **not** described to the model: grounding declarations are derived
deterministically from the rule head (``generation/grounding.py``), never authored
by the LLM (CLAUDE.md §5 invariant 4). The model is told to omit them.

Imports ``types`` and ``domain`` only (ARCHITECTURE.md §4).
"""

from __future__ import annotations

from rtec_llm.domain.spec import Fluent, Vocabulary
from rtec_llm.types import Message

_RTEC_PRIMER = """\
You are an expert in the Event Calculus and RTEC (Run-Time Event Calculus), a \
logic-programming engine for composite event recognition. You write RTEC event \
descriptions in Prolog.

RTEC conventions:
- Variables start with an uppercase letter; predicates and constants are lowercase.
- Each clause ends with a period; the head and body are separated by ":-".
- A fluent F with value V is written F=V. Boolean fluents use the value `true`.
- happensAt(E, T): event E occurs at time T.
- holdsAt(F=V, T): fluent F has value V at time T.
- holdsFor(F=V, I): I is the list of maximal intervals during which F=V holds.
- initiatedAt(F=V, T) / terminatedAt(F=V, T): a period of F=V is initiated / \
terminated at T.
- start(F=V) / end(F=V) are events that fire at the start / end of a maximal \
interval of F=V.
- Negation is negation-by-failure, written \\+ (e.g. \\+ holdsAt(...)).

There are exactly two ways to define a composite fluent. Choosing the wrong \
regime yields a rule that compiles but recognises the wrong intervals:

(a) Event-driven fluent (EDF) — defined by initiatedAt/terminatedAt rules. Use \
when the activity starts and stops on discrete events. The first body literal of \
an initiatedAt rule is a positive happensAt. Example:

    initiatedAt(withinArea(Vessel, AreaType)=true, T) :-
        happensAt(entersArea(Vessel, Area), T),
        areaType(Area, AreaType).
    terminatedAt(withinArea(Vessel, AreaType)=true, T) :-
        happensAt(leavesArea(Vessel, Area), T),
        areaType(Area, AreaType).

(b) Statically determined fluent (SDF) — defined by a single holdsFor(F=V, I) \
rule whose body combines the holdsFor intervals of OTHER fluents with the \
interval-algebra predicates union_all/2, intersect_all/2, \
relative_complement_all/3, and (for minimum-duration constraints) \
intDurGreater/3. Use when the activity is a relationship between the durations \
of other activities. Example:

    holdsFor(rendezVous(V1, V2)=true, I) :-
        holdsFor(proximity(V1, V2)=true, Ip),
        holdsFor(lowSpeed(V1)=true, Il1),
        holdsFor(lowSpeed(V2)=true, Il2),
        intersect_all([Ip, Il1, Il2], Ii),
        thresholds(rendezvousTime, RT),
        intDurGreater(Ii, RT, I).

Heuristic: "starts when ... ends when ..." with named events ⇒ EDF. "for as long \
as ..." / "while ..." combining other activities ⇒ SDF; "longer than a minimum \
duration" ⇒ SDF with intDurGreater.

Hard rules:
- Use ONLY events, fluents, values, background-knowledge predicates, and \
threshold keys from the vocabulary below. Never invent a predicate, fluent, \
value, or threshold.
- Do NOT write grounding/1, dynamicDomain/1, collectIntervals/1, or any other \
declaration — those are generated automatically. Output rule clauses only.
- Output ONLY the Prolog clauses for the requested fluent, inside a single \
```prolog code block, with no prose or explanation.\
"""


def build_messages(*, vocab: Vocabulary, fluent: Fluent, nl_spec: str) -> list[Message]:
    """System (primer + vocabulary) and user (spec + target head) message list."""
    system = f"{_RTEC_PRIMER}\n\n{render_vocabulary(vocab)}"
    return [
        Message(role="system", content=system),
        Message(role="user", content=_user_prompt(fluent, nl_spec)),
    ]


def _user_prompt(fluent: Fluent, nl_spec: str) -> str:
    regime = (
        "event-driven fluent (EDF)"
        if fluent.fluent_type == "EDF"
        else ("statically determined fluent (SDF)")
    )
    return (
        f"Define the {regime} `{_signature(fluent)}`.\n\n"
        f"Specification:\n{nl_spec.strip()}\n\n"
        f"Write the RTEC rule clause(s) that define `{fluent.name}` exactly as "
        f"specified, using only the vocabulary provided. Output a single ```prolog "
        f"code block containing the clauses and nothing else."
    )


# ---------------------------------------------------------------------------
# Vocabulary rendering — a compact, closed allow-list the model must stay within
# ---------------------------------------------------------------------------


def render_vocabulary(vocab: Vocabulary) -> str:
    """Render the vocabulary as a closed symbol allow-list for the system prompt."""
    sections: list[str] = [f"VOCABULARY for domain `{vocab.name}` (closed set — use only these):"]

    sections.append("\nInput events (happensAt):")
    sections += [f"  - {_pred(p.name, p.arg_names)} — {p.meaning}" for p in vocab.events]

    if vocab.input_fluents:
        sections.append("\nInput fluents (arrive as ready-made intervals; query with holdsFor):")
        sections += [_fluent_line(f) for f in vocab.input_fluents]

    sections.append("\nOutput fluents (what rules may define or reference):")
    sections += [_fluent_line(f) for f in vocab.output_fluents]

    if vocab.background_knowledge:
        sections.append("\nBackground-knowledge predicates:")
        for b in vocab.background_knowledge:
            note = f" [NOTE: {b.note}]" if b.note else ""
            sections.append(f"  - {_pred(b.name, b.arg_names)} — {b.meaning}{note}")

    if vocab.thresholds:
        sections.append("\nThreshold keys (use as thresholds(Key, Value)):")
        sections += [f"  - {t.key} (= {t.value}) — {t.meaning}" for t in vocab.thresholds]

    if vocab.value_domains:
        sections.append("\nValue domains (legal atoms):")
        sections += [
            f"  - {d.name}: {{{', '.join(d.atoms)}}} — {d.meaning}" for d in vocab.value_domains
        ]

    return "\n".join(sections)


def _fluent_line(f: Fluent) -> str:
    values = ", ".join(f.values)
    return f"  - {_pred(f.name, f.arg_names)}={{{values}}} [{f.fluent_type}] — {f.meaning}"


def _signature(f: Fluent) -> str:
    value = f.values[0] if f.values == ("true",) else "Value"
    return f"{_pred(f.name, f.arg_names)}={value}"


def _pred(name: str, arg_names: tuple[str, ...]) -> str:
    if not arg_names:
        return name
    return f"{name}({', '.join(_var(a) for a in arg_names)})"


def _var(arg_name: str) -> str:
    return arg_name[:1].upper() + arg_name[1:] if arg_name else "X"
