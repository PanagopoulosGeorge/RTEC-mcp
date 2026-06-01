"""Derive maritime fixture ground-truth files from a real RTEC engine run.

Runs the canonical ``examples/maritime`` event description over a single fixed
window via the engine adapter and writes one raw RTEC ``recognitions(...)`` file
per target fluent into ``rtec_llm/fixtures/data/msa/<fluent>.gt.txt``. The
committed hand-authored rules are the behavioural reference: a generated rule is
graded by whether it reproduces these intervals (CLAUDE.md §3). Half-open
``[s, e)`` is preserved exactly as RTEC emits it.

This lives outside ``rtec_llm/fixtures/`` on purpose — it imports the engine
adapter, which the fixtures package may not (ARCHITECTURE.md §4). Re-run to
regenerate:  ``python scripts/seed_maritime_fixtures.py``

Fixed window (docs/ENGINE_NOTES.md §2.2): start=1443650400 end=1443686400,
window_size=step=36000 (one ~10h Brest window).
"""

from __future__ import annotations

from pathlib import Path

from rtec_llm.adapters.engine import RtecSubprocessEngine
from rtec_llm.types import ExecutionResult, RecognisedFluent, Window

ROOT = Path(__file__).resolve().parents[1]
MARITIME = ROOT / "examples" / "maritime"
OUT_DIR = ROOT / "rtec_llm" / "fixtures" / "data" / "msa"

WINDOW = Window(start_time=1443650400, end_time=1443686400, window_size=36000, step=36000)
TARGET_FLUENTS = ("withinArea", "highSpeedNearCoast", "loitering", "rendezVous")


def _serialize(rf: RecognisedFluent) -> str:
    args = ",".join(rf.args)
    intervals = ",".join(f"({iv.start},{iv.end})" for iv in rf.intervals)
    return f"recognitions(predictions,{rf.fluent.name},[[{args}],{rf.fluent.value}],[{intervals}])."


def _run() -> ExecutionResult:
    aux = MARITIME / "resources" / "auxiliary"
    engine = RtecSubprocessEngine(app="maritime", repo_root=ROOT, timeout_s=300.0)
    return engine.run(
        rules=(MARITIME / "resources" / "patterns" / "rules.prolog").read_text(encoding="utf-8"),
        declarations=None,
        event_stream=MARITIME / "dataset" / "csv" / "brest_critical.csv",
        window=WINDOW,
        static_data=(aux / "compare.prolog", aux / "loadStaticData.prolog"),
    )


def main() -> int:
    result = _run()
    fatal = [e for e in result.errors if e.kind in {"compile_error", "runtime_error", "timeout"}]
    if fatal:
        print("engine run failed:", fatal)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fluent in TARGET_FLUENTS:
        lines = [
            _serialize(rf)
            for rf in result.recognised
            if rf.fluent.name == fluent and rf.intervals
        ]
        gt_path = OUT_DIR / f"{fluent}.gt.txt"
        gt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        timepoints = sum(
            iv.end - iv.start
            for rf in result.recognised
            if rf.fluent.name == fluent
            for iv in rf.intervals
        )
        print(f"wrote {gt_path.relative_to(ROOT)}: {len(lines)} instance(s), {timepoints} timepoints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())