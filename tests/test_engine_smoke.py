"""Smoke tests for the RTEC engine adapter — run the REAL engine, no mocks.

These prove the executor seam end-to-end: rules + event stream go in, the actual
``swipl`` engine runs, and the adapter returns typed intervals that match known
recognition output. Per CLAUDE.md §3/§5, RTEC execution is the *only* correctness
signal, so this path must never be mocked.

Wired into ``make smoke-test``. Skips (rather than fails) only when the engine or
a large gitignored dataset is unavailable, since neither is something the adapter
can fix.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from rtec_llm.adapters.engine import RtecSubprocessEngine
from rtec_llm.ports.engine import EnginePort
from rtec_llm.types import Interval, RecognisedFluent, Window

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "examples" / "toy"
MARITIME = ROOT / "examples" / "maritime"

requires_swipl = pytest.mark.skipif(
    shutil.which("swipl") is None,
    reason="swipl not on PATH; the real RTEC engine cannot run (CLAUDE.md §6).",
)


def _by_instance(
    result_recognised: tuple[RecognisedFluent, ...],
) -> dict[tuple[str, str, tuple[str, ...]], tuple[Interval, ...]]:
    """Index recognised fluents by (name, value, args) for assertion lookups."""
    return {(rf.fluent.name, rf.fluent.value, rf.args): rf.intervals for rf in result_recognised}


def test_adapter_satisfies_engine_port() -> None:
    """Structural check: the adapter is a valid EnginePort implementation."""
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    assert isinstance(engine, EnginePort)


@requires_swipl
def test_toy_recognises_known_intervals() -> None:
    """The toy event description reproduces its five known recognised intervals."""
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    result = engine.run(
        rules=(TOY / "resources" / "patterns" / "rules.prolog").read_text(encoding="utf-8"),
        declarations=None,
        event_stream=TOY / "dataset" / "csv" / "toy_data.csv",
        window=Window(start_time=0, end_time=50, window_size=50, step=50),
        static_data=(TOY / "dataset" / "auxiliary" / "toy_var_domain.prolog",),
    )

    assert result.errors == (), f"unexpected engine errors: {result.errors}"
    got = _by_instance(result.recognised)
    assert got[("rich", "true", ("chris",))] == (Interval(14, 20),)
    assert got[("location", "pub", ("chris",))] == (Interval(18, 22),)
    assert got[("location", "home", ("chris",))] == (Interval(22, 51),)
    assert got[("location", "work", ("chris",))] == (Interval(10, 18),)
    assert got[("happy", "true", ("chris",))] == (Interval(14, 22),)


@requires_swipl
def test_maritime_within_area_simple_fluent() -> None:
    """A maritime EDF (withinArea) reproduces a known recognised interval set.

    ``withinArea`` is a simple/event-driven fluent (initiatedAt on entersArea,
    terminatedAt on leavesArea). Expected intervals are the documented result for
    vessel 209273000 over the first 36000s window (docs/ENGINE_NOTES.md §2.2).
    """
    event_stream = MARITIME / "dataset" / "csv" / "brest_critical.csv"
    if not event_stream.is_file():
        pytest.skip(
            "maritime AIS dataset (brest_critical.csv) not present; it is downloaded separately."
        )

    aux = MARITIME / "resources" / "auxiliary"
    engine = RtecSubprocessEngine(app="maritime", repo_root=ROOT, timeout_s=300.0)
    result = engine.run(
        rules=(MARITIME / "resources" / "patterns" / "rules.prolog").read_text(encoding="utf-8"),
        declarations=None,
        event_stream=event_stream,
        window=Window(start_time=1443650400, end_time=1443686400, window_size=36000, step=36000),
        static_data=(aux / "compare.prolog", aux / "loadStaticData.prolog"),
    )

    fatal = [e for e in result.errors if e.kind in {"compile_error", "runtime_error", "timeout"}]
    assert not fatal, f"engine failed: {fatal}"
    assert result.recognised, "maritime run produced no recognised intervals"

    got = _by_instance(result.recognised)
    assert got[("withinArea", "true", ("209273000", "fishing"))] == (
        Interval(1443681546, 1443681669),
        Interval(1443681895, 1443681955),
        Interval(1443684266, 1443685589),
        Interval(1443685596, 1443686401),
    )


@requires_swipl
def test_compile_error_is_typed_not_raised() -> None:
    """A syntactically broken rule yields a typed compile_error, not an exception."""
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    result = engine.run(
        rules="initiatedAt(broken(X)=true, T) :-\n    happensAt(go_to(X, _), T)\n",  # missing '.'
        declarations=None,
        event_stream=TOY / "dataset" / "csv" / "toy_data.csv",
        window=Window(start_time=0, end_time=50, window_size=50, step=50),
        static_data=(TOY / "dataset" / "auxiliary" / "toy_var_domain.prolog",),
    )

    assert result.recognised == ()
    assert [e.kind for e in result.errors] == ["compile_error"]
    assert result.errors[0].message, "compile error must surface engine stderr, not swallow it"


@requires_swipl
def test_runtime_error_is_typed_not_raised() -> None:
    """A rule that compiles but calls an undefined predicate yields runtime_error.

    The orchestrator routes on ``kind``, so runtime failures must be a distinct,
    real error type — not a crash and not misfiled as a compile error.
    """
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    result = engine.run(
        rules=(
            "initiatedAt(foo(X)=true, T) :- happensAt(go_to(X, _), T), nope_undefined(X).\n"
            "grounding(foo(Person)=true) :- person(Person).\n"
        ),
        declarations=None,
        event_stream=TOY / "dataset" / "csv" / "toy_data.csv",
        window=Window(start_time=0, end_time=50, window_size=50, step=50),
        static_data=(TOY / "dataset" / "auxiliary" / "toy_var_domain.prolog",),
    )

    assert result.recognised == ()
    assert any(e.kind == "runtime_error" for e in result.errors), [e.kind for e in result.errors]
    assert result.errors[0].message, "runtime error must surface engine stderr, not swallow it"


@requires_swipl
def test_empty_output_is_typed() -> None:
    """Valid rules over an event-free window yield a typed empty_output signal.

    Empty recognition is not a crash; the orchestrator needs to tell it apart
    from a compile/runtime failure, so it must surface as its own ``kind``.
    """
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    result = engine.run(
        rules=(TOY / "resources" / "patterns" / "rules.prolog").read_text(encoding="utf-8"),
        declarations=None,
        event_stream=TOY / "dataset" / "csv" / "toy_data.csv",
        window=Window(start_time=100, end_time=150, window_size=50, step=50),  # no events here
        static_data=(TOY / "dataset" / "auxiliary" / "toy_var_domain.prolog",),
    )

    assert result.recognised == ()
    assert [e.kind for e in result.errors] == ["empty_output"]


@requires_swipl
def test_run_leaves_inplace_tree_untouched() -> None:
    """Isolation (CLAUDE.md §6 #8): a run never regenerates files under examples/.

    The engine compiles to a temp dir, so ``examples/<app>/.../compiled_rules.prolog``
    must be byte-for-byte identical before and after. Toy stands in for every app —
    the compile-output path derives from the (temp) rules path identically.
    """
    patterns = TOY / "resources" / "patterns"

    def digest() -> dict[str, str]:
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(patterns.iterdir())
            if p.is_file()
        }

    before = digest()
    engine = RtecSubprocessEngine(app="toy", repo_root=ROOT)
    engine.run(
        rules=(patterns / "rules.prolog").read_text(encoding="utf-8"),
        declarations=None,
        event_stream=TOY / "dataset" / "csv" / "toy_data.csv",
        window=Window(start_time=0, end_time=50, window_size=50, step=50),
        static_data=(TOY / "dataset" / "auxiliary" / "toy_var_domain.prolog",),
    )
    assert digest() == before, "adapter modified files under examples/ (isolation breach)"
