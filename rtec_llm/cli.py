"""Thin CLI entry point for ``rtec_llm`` — the composition root.

This is the *only* place that instantiates concrete adapters (engine / scoring /
LLM) and wires them into a run; every other module depends on ports and types
(ARCHITECTURE.md §4). The first command is the single-shot **baseline**:

    python -m rtec_llm.cli baseline --domain msa --provider openai [--fluent NAME]

For each fixture it runs the oracle pipeline exactly once — **generate → execute
→ compare, no repair** — and records the result. This is the control arm that
shows whether the (future) repair loop adds value, and it forces the whole
pipeline end-to-end. Success is **F1 from real RTEC execution**, never a clean
compile (CLAUDE.md §3, §5 invariant 2).

Per-target the LLM authors only the requested fluent. Its prerequisite fluents
(``FixtureSpec.prerequisite_fluents`` and their transitive dependencies) are
scaffolded from the canonical reference event description so the target is scored
on a fair footing, exactly as the GT was produced (toggle with
``--no-prerequisites`` for the pure single-rule control). Grounding for every
fluent in the assembled rule set is generated deterministically from its head
(``generation/grounding.py``); the static input environment (event/input
groundings, dynamic domains, fallbacks) is taken verbatim from the reference
event description. The LLM never authors a grounding declaration.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rtec_llm.adapters.engine import RtecSubprocessEngine
from rtec_llm.adapters.llm import make_llm
from rtec_llm.adapters.scoring import RtecScorer, macro_f1_by_fluent_type
from rtec_llm.domain import Vocabulary, load_domain
from rtec_llm.domain.spec import Fluent
from rtec_llm.fixtures import FixtureSpec, load_fixtures
from rtec_llm.generation import GeneratedRule, generate
from rtec_llm.generation import grounding as generation_grounding
from rtec_llm.ports.engine import EnginePort
from rtec_llm.ports.llm import LLMPort
from rtec_llm.ports.scoring import ScoringPort
from rtec_llm.types import ExecutionResult, Message, ScoreResult

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FATAL_ERROR_KINDS = frozenset({"compile_error", "runtime_error", "timeout"})


@dataclass(frozen=True, slots=True)
class _DomainRun:
    """Engine wiring for a domain: which RTEC app, reference rules, static data."""

    app: str
    reference_rules: Path
    static_data: tuple[Path, ...]


def _domain_run(domain: str) -> _DomainRun:
    if domain == "msa":
        maritime = _REPO_ROOT / "examples" / "maritime"
        aux = maritime / "resources" / "auxiliary"
        return _DomainRun(
            app="maritime",
            reference_rules=maritime / "resources" / "patterns" / "rules.prolog",
            static_data=(aux / "compare.prolog", aux / "loadStaticData.prolog"),
        )
    raise NotImplementedError(
        f"baseline engine wiring is only defined for domain 'msa'; got {domain!r}"
    )


# ---------------------------------------------------------------------------
# One fluent's outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Outcome:
    fluent_name: str
    fluent_type: str
    generated: GeneratedRule
    execution: ExecutionResult
    score: ScoreResult
    assembled_rules: str
    declarations: str

    @property
    def compiled(self) -> bool:
        return not any(e.kind == "compile_error" for e in self.execution.errors)

    @property
    def error_kinds(self) -> str:
        return ";".join(e.kind for e in self.execution.errors)


def run_one(
    *,
    fixture: FixtureSpec,
    vocab: Vocabulary,
    domain_run: _DomainRun,
    llm: LLMPort,
    engine: EnginePort,
    scorer: ScoringPort,
    model: str,
    temperature: float,
    scaffold_prerequisites: bool,
) -> _Outcome:
    """Generate one rule, execute it once, and score it against ground truth."""
    fluent = _require_fluent(vocab, fixture.fluent_name)
    generated = generate(
        fluent=fluent,
        nl_spec=fixture.nl_spec,
        vocab=vocab,
        llm=llm,
        model=model,
        temperature=temperature,
    )

    assembled_rules = generated.rules
    if scaffold_prerequisites:
        prereq_rules = _prerequisite_rules(
            domain_run.reference_rules, vocab, fixture.fluent_name, fixture.prerequisite_fluents
        )
        if prereq_rules:
            assembled_rules = (
                f"{generated.rules}\n\n% --- scaffolded prerequisites ---\n{prereq_rules}"
            )

    declarations = _build_declarations(domain_run, vocab, assembled_rules, generated)

    execution = engine.run(
        rules=assembled_rules,
        declarations=declarations,
        event_stream=fixture.event_stream_ref,
        window=fixture.window,
        static_data=domain_run.static_data,
    )
    # The oracle: F1 from real execution. Score ONLY the target fluent's
    # recognitions — the ground-truth file contains just the target, so the
    # scaffolded prerequisite fluents (withinArea, stopped, …) the engine also
    # emits must not be counted as false positives. Empty output (e.g. after a
    # compile error) scores honestly as all-false-negatives, never as a pass.
    predicted = ExecutionResult(
        recognised=tuple(
            rf for rf in execution.recognised if rf.fluent.name == fixture.fluent_name
        ),
        errors=execution.errors,
        wall_time_ms=execution.wall_time_ms,
    )
    score = scorer.score(predicted, fixture.ground_truth_file, fixture.fluent_type)
    return _Outcome(
        fluent_name=fixture.fluent_name,
        fluent_type=fixture.fluent_type,
        generated=generated,
        execution=execution,
        score=score,
        assembled_rules=assembled_rules,
        declarations=declarations,
    )


def _require_fluent(vocab: Vocabulary, name: str) -> Fluent:
    fluent = vocab.fluent(name)
    if fluent is None:
        raise ValueError(f"fluent {name!r} is not in the {vocab.name!r} vocabulary")
    return fluent


# ---------------------------------------------------------------------------
# Declarations: static input environment (from reference) + generated groundings
# ---------------------------------------------------------------------------


def _build_declarations(
    domain_run: _DomainRun, vocab: Vocabulary, assembled_rules: str, generated: GeneratedRule
) -> str:
    """Static input env (reference) + deterministic output-fluent groundings."""
    preamble = _static_preamble(domain_run.reference_rules, vocab)
    output_groundings = generation_grounding.grounding_for_rules(assembled_rules, vocab)
    parts = [preamble, output_groundings]
    if generated.grounding and generated.grounding not in output_groundings:
        parts.append(generated.grounding)
    return "\n\n".join(p for p in parts if p.strip())


def _static_preamble(reference_rules: Path, vocab: Vocabulary) -> str:
    """The non-output-fluent declarations of the reference event description.

    Keeps ``dynamicDomain``/``collectIntervals``/``needsGrounding``/
    ``buildFromPoints``/``index`` declarations and the ``grounding/1`` clauses for
    events and input fluents — the fixed input environment every run shares.
    Output-fluent groundings are dropped here because they are regenerated
    deterministically from the assembled rule heads.
    """
    input_functors = vocab.event_names() | vocab.input_fluent_names()
    keep: list[str] = []
    for clause in generation_grounding.split_clauses(reference_rules.read_text(encoding="utf-8")):
        stripped = clause.lstrip()
        if stripped.startswith(
            ("dynamicDomain(", "collectIntervals(", "needsGrounding(", "buildFromPoints(", "index(")
        ):
            keep.append(clause + ".")
            continue
        if stripped.startswith("grounding("):
            functor = generation_grounding.grounded_functor(clause)
            if functor is not None and functor in input_functors:
                keep.append(clause + ".")
    return "\n".join(keep)


# ---------------------------------------------------------------------------
# Prerequisite scaffolding from the canonical reference event description
# ---------------------------------------------------------------------------


def _rule_clauses_by_fluent(reference_rules: Path) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for clause in generation_grounding.split_clauses(reference_rules.read_text(encoding="utf-8")):
        functor = generation_grounding.clause_fluent(clause)
        if functor is not None:
            grouped.setdefault(functor, []).append(clause + ".")
    return grouped


def _prerequisite_rules(
    reference_rules: Path,
    vocab: Vocabulary,
    target: str,
    prerequisites: Sequence[str],
) -> str:
    """Reference rule clauses for the transitive closure of ``prerequisites``.

    Input fluents (e.g. ``proximity``) have no defining rules — they arrive as
    intervals and are handled by the static preamble's ``collectIntervals``. The
    target fluent's own clauses are excluded: the LLM authors those.
    """
    by_fluent = _rule_clauses_by_fluent(reference_rules)
    output_names = vocab.output_fluent_names()

    needed: set[str] = set()
    frontier: list[str] = [p for p in prerequisites if p in output_names]
    while frontier:
        fluent = frontier.pop()
        if fluent == target or fluent in needed:
            continue
        needed.add(fluent)
        for clause in by_fluent.get(fluent, []):
            for ref in generation_grounding.referenced_functors(clause):
                if ref != target and ref in output_names and ref not in needed:
                    frontier.append(ref)
    needed.discard(target)

    ordered = [f for f in by_fluent if f in needed]
    blocks = [clause for fluent in ordered for clause in by_fluent[fluent]]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Offline "reference" provider — perfect-generation control / pipeline check
# ---------------------------------------------------------------------------


class _ReferenceLLM:
    """An offline ``LLMPort`` that returns a fluent's canonical reference rule.

    Not a real model: it ignores the prompt and replays the hand-written clauses
    for the fluent named in ``--fluent`` selection order. Useful as an upper-bound
    control (a *perfect* single-shot generator should score ~1.0) and to exercise
    the full CLI path without network access. Constructed only here, in the
    composition root, so reading the reference event description never leaks into
    an adapter.
    """

    def __init__(self, reference_rules: Path) -> None:
        self._by_fluent = _rule_clauses_by_fluent(reference_rules)
        self._fluent: str | None = None

    def for_fluent(self, fluent_name: str) -> _ReferenceLLM:
        self._fluent = fluent_name
        return self

    def complete(self, *, messages: list[Message], model: str, temperature: float) -> str:
        clauses = self._by_fluent.get(self._fluent or "", [])
        body = "\n".join(clauses)
        return f"```prolog\n{body}\n```"


# ---------------------------------------------------------------------------
# Recording + reporting
# ---------------------------------------------------------------------------


def _write_csv(path: Path, outcomes: Sequence[_Outcome]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "fluent_name",
                "fluent_type",
                "f1",
                "precision",
                "recall",
                "compiled",
                "n_iterations",
                "error_kind",
            ]
        )
        for o in outcomes:
            writer.writerow(
                [
                    o.fluent_name,
                    o.fluent_type,
                    f"{o.score.f1:.6f}",
                    f"{o.score.precision:.6f}",
                    f"{o.score.recall:.6f}",
                    "true" if o.compiled else "false",
                    1,
                    o.error_kinds,
                ]
            )


def _log_generated(log_dir: Path, outcome: _Outcome) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{outcome.fluent_name}.raw.txt").write_text(
        outcome.generated.raw_completion, encoding="utf-8"
    )
    assembled = (
        f"% ===== declarations (deterministic; not LLM-authored) =====\n"
        f"{outcome.declarations}\n\n"
        f"% ===== rules (target authored by LLM; prerequisites scaffolded) =====\n"
        f"{outcome.assembled_rules}\n"
    )
    (log_dir / f"{outcome.fluent_name}.assembled.prolog").write_text(assembled, encoding="utf-8")


def _print_summary(outcomes: Sequence[_Outcome], csv_path: Path) -> None:
    print("\n" + "=" * 72)
    print("BASELINE RESULTS (single-shot, no repair) — F1 from real RTEC execution")
    print("=" * 72)
    header = f"{'fluent':<22}{'type':<6}{'F1':>8}{'prec':>8}{'rec':>8}{'compiled':>10}"
    print(header)
    print("-" * 72)
    for o in sorted(outcomes, key=lambda x: (x.fluent_type, x.fluent_name)):
        print(
            f"{o.fluent_name:<22}{o.fluent_type:<6}"
            f"{o.score.f1:>8.3f}{o.score.precision:>8.3f}{o.score.recall:>8.3f}"
            f"{('yes' if o.compiled else 'NO'):>10}"
        )

    print("-" * 72)
    by_type = macro_f1_by_fluent_type([o.score for o in outcomes])
    print("Macro F1 by fluent type (the central EDF/SDF thesis split):")
    for fluent_type in ("EDF", "SDF"):
        if fluent_type in by_type:
            count = sum(1 for o in outcomes if o.fluent_type == fluent_type)
            print(f"  {fluent_type}: {by_type[fluent_type]:.3f}  (n={count})")
    print(f"\nPer-fluent CSV: {csv_path}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def _baseline(args: argparse.Namespace) -> int:
    _load_dotenv(_REPO_ROOT / ".env")
    domain: str = args.domain
    domain_run = _domain_run(domain)
    vocab = load_domain(domain)

    fixtures = [
        f for f in load_fixtures(domain) if args.fluent is None or f.fluent_name == args.fluent
    ]
    if not fixtures:
        target = f" matching --fluent {args.fluent!r}" if args.fluent else ""
        print(f"no fixtures found for domain {domain!r}{target}.", file=sys.stderr)
        return 2

    reference_llm: _ReferenceLLM | None = None
    base_llm: LLMPort
    if args.provider == "reference":
        reference_llm = _ReferenceLLM(domain_run.reference_rules)
        base_llm = reference_llm
    else:
        base_llm = make_llm(args.provider)

    engine = RtecSubprocessEngine(app=domain_run.app, repo_root=_REPO_ROOT, timeout_s=args.timeout)
    scorer = RtecScorer()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    results_dir = _REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / f"baseline_{args.provider}_{timestamp}.csv"
    log_dir = results_dir / f"baseline_{args.provider}_{timestamp}"

    outcomes: list[_Outcome] = []
    for fixture in fixtures:
        print(f"\n>> {fixture.fluent_name} [{fixture.fluent_type}] — generating (1 shot)…")
        llm = reference_llm.for_fluent(fixture.fluent_name) if reference_llm else base_llm
        try:
            outcome = run_one(
                fixture=fixture,
                vocab=vocab,
                domain_run=domain_run,
                llm=llm,
                engine=engine,
                scorer=scorer,
                model=args.model,
                temperature=args.temperature,
                scaffold_prerequisites=not args.no_prerequisites,
            )
        except Exception as exc:  # surface, record, and continue the sweep
            print(f"   !! generation/execution failed: {exc}", file=sys.stderr)
            continue

        _log_generated(log_dir, outcome)
        print("   raw generated Prolog:")
        print(_indent(outcome.generated.rules or "(empty)"))
        status = "compiled" if outcome.compiled else f"NOT compiled ({outcome.error_kinds})"
        print(
            f"   → F1={outcome.score.f1:.3f} "
            f"P={outcome.score.precision:.3f} R={outcome.score.recall:.3f} "
            f"(tp={outcome.score.tp} fp={outcome.score.fp} fn={outcome.score.fn}; {status})"
        )
        outcomes.append(outcome)

    if not outcomes:
        print("no fluents completed.", file=sys.stderr)
        return 1

    _write_csv(csv_path, outcomes)
    _print_summary(outcomes, csv_path)
    return 0


def _indent(text: str, prefix: str = "      ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _load_dotenv(path: Path) -> None:
    """Load ``KEY=VALUE`` lines from ``.env`` into the environment (no overwrite)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rtec_llm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    baseline = sub.add_parser(
        "baseline",
        help="single-shot generate→execute→compare per fluent (no repair; the control arm)",
    )
    baseline.add_argument("--domain", default="msa", help="domain vocabulary (default: msa)")
    baseline.add_argument(
        "--provider",
        default="openai",
        help="LLM provider: openai (default) | anthropic | gemini | ollama | glm | "
        "reference (offline perfect-generation control)",
    )
    baseline.add_argument("--model", default="gpt-4o", help="model id (default: gpt-4o)")
    baseline.add_argument(
        "--temperature", type=float, default=0.0, help="sampling temperature (default: 0.0)"
    )
    baseline.add_argument("--fluent", default=None, help="run a single target fluent by name")
    baseline.add_argument(
        "--no-prerequisites",
        action="store_true",
        help="do NOT scaffold prerequisite fluents from the reference rules "
        "(pure single-rule control; SDFs will score ~0 without their inputs)",
    )
    baseline.add_argument(
        "--timeout", type=float, default=600.0, help="per-run engine timeout in seconds"
    )
    baseline.set_defaults(func=_baseline)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    result = func(args)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
