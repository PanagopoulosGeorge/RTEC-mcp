"""
Leak-safe counterexample generator (Course M3).

Two layers:
  Layer 1 (STRATEGY): precision/recall -> failure mode -> directive. Scalar, leak-free.
  Layer 2 (COUNTEREXAMPLE): concrete witnesses, leakage-governed:
    - false_positives = generated - gold  -> synth's OWN output -> cite verbatim (safe)
    - false_negatives = gold - generated  -> gold only -> NEVER verbatim (the answer)

REDACTION is the leakage knob (ablation axis):
  "none"    -> FN intervals verbatim       (cheater upper-bound, scientifically void)
  "witness" -> FN onset only, extent hidden (honest CEGIS, default)
  "scalar"  -> no FN locations              (feedback-injection lower-bound)
"""

from dataclasses import dataclass

from .schemas import EvalReport, IntervalDiff, FluentScore


REDACTION = "witness"
MAX_FLUENTS = 5       # context-pollution guard: worst-N fluents only
MAX_INTERVALS = 3     # per-fluent cap on cited intervals


@dataclass
class _Diag:
    mode: str
    directive: str


def _diagnose(s: FluentScore) -> _Diag:
    P, R = s.precision, s.recall
    if s.tp == 0:
        return _Diag("NO-OVERLAP",
                     "your rule never coincides with the target — likely the wrong triggering "
                     "event or the wrong fluent value; rethink structure.")
    if P >= 0.8 and R < 0.6:
        return _Diag("UNDER-FIRING (too strict)",
                     "relax preconditions and check for a premature terminatedAt; the rule is "
                     "right where it fires but misses most cases.")
    if R >= 0.8 and P < 0.6:
        return _Diag("OVER-FIRING (too loose)",
                     "add guards/preconditions; the rule fires where it should AND where it shouldn't.")
    if P < 0.6 and R < 0.6:
        return _Diag("STRUCTURALLY WRONG",
                     "low on both axes — wrong event, wrong fluent kind, or a missing dependency; "
                     "needs a structural rewrite, not tuning.")
    return _Diag("BOUNDARY ERROR",
                 "close on both axes — likely off-by-small intervals; check timing/inertia and edges.")


def _fmt(intervals, n=MAX_INTERVALS) -> str:
    shown = ", ".join(f"({s},{e})" for s, e in intervals[:n])
    more = f" (+{len(intervals) - n} more)" if len(intervals) > n else ""
    return shown + more


def _fn_evidence(diff: IntervalDiff, raw_events: dict | None) -> str:
    """Layer 2 for FNs — leakage-governed. NEVER prints the FN interval verbatim."""
    if not diff.false_negatives:
        return ""
    if REDACTION == "none":
        return f"    MISSING (gold): {_fmt(diff.false_negatives)}"
    if REDACTION == "scalar":
        return "    you are missing coverage for this fluent (no locations given)."
    onset = diff.false_negatives[0][0]
    line = (f"    under-covered: your rule is silent around t={onset} where it should fire "
            f"(extent withheld; {len(diff.false_negatives)} missing region(s) total).")
    if raw_events and onset in raw_events:
        line += f"\n    raw input events near t={onset}: {raw_events[onset]}"
    return line


def to_feedback(report: EvalReport, raw_events: dict | None = None) -> str:
    """Synthesizer-visible counterexample string. The leakage firewall."""
    imperfect = sorted((s for s in report.per_fluent if s.f1 < 1.0), key=lambda s: s.f1)
    if not imperfect:
        return "All in-scope fluents match the reference. Done."

    diff_by_key = {(d.fluent, d.value): d for d in report.diffs}
    out = [f"Overall micro-F1={report.micro_f1:.2f}, macro-F1={report.macro_f1:.2f}. "
           f"Fix the worst fluents first:\n"]

    for s in imperfect[:MAX_FLUENTS]:
        d = _diagnose(s)
        out.append(f"Fluent {s.fluent}={s.value}  |  F1={s.f1:.2f} "
                   f"(P={s.precision:.2f}, R={s.recall:.2f}) — {d.mode}")
        out.append(f"    strategy: {d.directive}")
        diff = diff_by_key.get((s.fluent, s.value))
        if diff and diff.false_positives:
            out.append(f"    spurious (your output): {_fmt(diff.false_positives)}")
        if diff:
            fn = _fn_evidence(diff, raw_events)
            if fn:
                out.append(fn)
        out.append("")
    return "\n".join(out)
