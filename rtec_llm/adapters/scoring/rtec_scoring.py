"""``ScoringPort`` adapter that wraps ``execution scripts/scoring/utilities/``.

The wrapped utilities (``parser.py``, ``compare.py``, ``temporal_ops.py``)
already compute the point-set timepoint F1 the thesis uses as its oracle —
half-open ``[s, e)`` semantics, summed to ``end - start`` per interval. This
module is a *thin wrapper*: it does not reimplement TP / FP / FN, precision,
recall, F1, or the micro / macro reducers. The only net-new logic is the
disagreement sampler (built on ``temporal_ops.temporal_difference``).

**Parser-ownership contract (ARCHITECTURE.md §7 P3 / §8 P2-2).** Predicted
intervals arrive already typed in the ``ExecutionResult`` produced by the engine
adapter — the sole parser of RTEC's predicted output. This adapter adapts those
typed objects to the utilities' dict shape *in memory* (a typed→dict transform,
not a string parse, not a file read) and parses *only* the ground-truth file via
``parser.parse_file``. There is exactly one parse path for predictions; this
layer never re-parses an RTEC result file.

**Loading the utilities.** They live at ``execution scripts/scoring/`` — a
directory with a space in its name, not importable as a regular package. We add
that directory to ``sys.path`` and import the ``utilities`` package
(``utilities/__init__.py`` exists, and its modules import each other as
``from utilities.X import ...``). The boundary import carries one narrow
``# type: ignore`` because the wrapped modules have no type annotations; do NOT
``import evaluate`` (its CSV-writing block runs at module import — see CLAUDE.md
"Scoring runs" gotchas).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from rtec_llm.types import (
    Disagreement,
    DisagreementKind,
    ExecutionResult,
    FluentType,
    RecognisedFluent,
    ScoreResult,
)

_SCORING_DIR = Path(__file__).resolve().parents[3] / "execution scripts" / "scoring"
if str(_SCORING_DIR) not in sys.path:
    sys.path.insert(0, str(_SCORING_DIR))

from utilities import compare, parser, temporal_ops  # type: ignore[import-not-found]  # noqa: E402

# Internal dict shape consumed by ``compare.compare_ce`` / ``parser.parse_file``:
#   {(fluent_name, value): {args_tuple: [[start, end], ...]}}
_FvpKey = tuple[str, str]
_ArgsKey = tuple[str, ...]
_IntervalList = list[list[int]]
_CompareDict = dict[_FvpKey, dict[_ArgsKey, _IntervalList]]

_DEFAULT_OFF_BY_ONE_TOLERANCE = 1
_DEFAULT_MAX_DISAGREEMENT_SAMPLES = 50


class RtecScorer:
    """Point-set timepoint F1 scorer; satisfies :class:`rtec_llm.ports.scoring.ScoringPort`.

    Args:
        off_by_one_tolerance: Maximum duration (timepoints) of a disagreement
            region still treated as a boundary off-by-one when it abuts a match.
            Defaults to ``1`` (one clock tick in the maritime domain, where
            ``clock_tick = 1``). Domains with a coarser tick (e.g. CAVIAR's
            ``clock_tick = 40``) may pass a larger tolerance.
        max_disagreement_samples: Upper bound on the number of classified
            disagreement timestamps carried on each ``ScoreResult`` — a bounded
            (memory-safe) sample, not the full divergence set.
    """

    def __init__(
        self,
        *,
        off_by_one_tolerance: int = _DEFAULT_OFF_BY_ONE_TOLERANCE,
        max_disagreement_samples: int = _DEFAULT_MAX_DISAGREEMENT_SAMPLES,
    ) -> None:
        self._tolerance = off_by_one_tolerance
        self._max_samples = max_disagreement_samples

    def score(
        self,
        predicted: ExecutionResult,
        ground_truth_file: Path,
        fluent_type: FluentType,
    ) -> ScoreResult:
        """Score one prediction against a ground-truth file; see ``ScoringPort``."""
        test_dict = _to_compare_dict(predicted.recognised)
        gt_dict: _CompareDict = parser.parse_file(ground_truth_file)

        # compare_ce per fluent-value pair over the union of GT and predicted
        # FVPs. The second pass catches FVPs present only in the prediction
        # (all false positives) — evaluate.py drops these; we must not (the
        # CLAUDE.md "Scoring runs" gotcha).
        per_fvp: dict[_FvpKey, dict[str, float]] = {}
        for fvp in gt_dict:
            per_fvp[fvp] = compare.compare_ce(gt_dict[fvp], test_dict.get(fvp))
        for fvp in test_dict:
            if fvp not in gt_dict:
                per_fvp[fvp] = compare.compare_ce({}, test_dict[fvp])

        if per_fvp:
            agg = compare.get_micro(per_fvp)
            tp, fp, fn = int(agg["tp"]), int(agg["fp"]), int(agg["fn"])
            precision = float(agg["precision"])
            recall = float(agg["recall"])
            f1 = float(agg["f1"])
        else:
            tp = fp = fn = 0
            precision = recall = f1 = 0.0

        return ScoreResult(
            tp=tp,
            fp=fp,
            fn=fn,
            precision=precision,
            recall=recall,
            f1=f1,
            fluent_type=fluent_type,
            disagreements=self._sample_disagreements(gt_dict, test_dict),
        )

    def _sample_disagreements(
        self, gt_dict: _CompareDict, test_dict: _CompareDict
    ) -> tuple[Disagreement, ...]:
        """Classify and sample where prediction and ground truth diverge.

        For each (FVP, args) instance: ``temporal_difference(test, gt)`` gives
        the false-positive regions and ``temporal_difference(gt, test)`` the
        false-negative regions. A region of duration ``<= tolerance`` that abuts
        a matched region (its start is a match end, or its end is a match start)
        is a ``boundary_off_by_one``; otherwise it is over-/under-production.
        Returns a deduplicated, temporally ordered, bounded sample.
        """
        samples: list[Disagreement] = []
        for fvp in set(gt_dict) | set(test_dict):
            gt_args = gt_dict.get(fvp, {})
            test_args = test_dict.get(fvp, {})
            for args in set(gt_args) | set(test_args):
                gt_iv = gt_args.get(args, [])
                test_iv = test_args.get(args, [])
                matches = temporal_ops.temporal_intersection(gt_iv, test_iv)
                match_starts = {int(m[0]) for m in matches}
                match_ends = {int(m[1]) for m in matches}
                for region in temporal_ops.temporal_difference(test_iv, gt_iv):
                    samples.append(
                        self._classify(region, "over_production", match_starts, match_ends)
                    )
                for region in temporal_ops.temporal_difference(gt_iv, test_iv):
                    samples.append(
                        self._classify(region, "under_production", match_starts, match_ends)
                    )

        deduped = list(dict.fromkeys(samples))
        deduped.sort(key=lambda d: d.timestamp)
        return tuple(deduped[: self._max_samples])

    def _classify(
        self,
        region: Sequence[int],
        base_kind: DisagreementKind,
        match_starts: set[int],
        match_ends: set[int],
    ) -> Disagreement:
        start, end = int(region[0]), int(region[1])
        adjacent_to_match = start in match_ends or end in match_starts
        if (end - start) <= self._tolerance and adjacent_to_match:
            kind: DisagreementKind = "boundary_off_by_one"
        else:
            kind = base_kind
        return Disagreement(timestamp=start, kind=kind)


def _to_compare_dict(recognised: tuple[RecognisedFluent, ...]) -> _CompareDict:
    """Adapt typed predicted intervals to the scoring utilities' dict shape.

    A typed→dict transform (``Interval(start, end)`` → ``[start, end]``), keyed
    by ``(fluent_name, value)`` then by args-tuple — matching what
    ``parser.parse_file`` returns for the ground truth, so the two sides line up
    in ``compare_ce``. This does NOT parse anything; the engine adapter already
    owns prediction parsing (parser-ownership contract). The defensive temporal
    union mirrors ``parse_file`` for the (engine-guaranteed-unique, but cheap to
    guard) case of a repeated ``(fluent, value, args)`` instance.
    """
    out: _CompareDict = {}
    for rf in recognised:
        key: _FvpKey = (rf.fluent.name, rf.fluent.value)
        intervals: _IntervalList = [[iv.start, iv.end] for iv in rf.intervals]
        bucket = out.setdefault(key, {})
        if rf.args in bucket:
            bucket[rf.args] = temporal_ops.temporal_union(bucket[rf.args], intervals)
        else:
            bucket[rf.args] = intervals
    return out


# ---------------------------------------------------------------------------
# Aggregation — expose the utilities' micro / macro reducers as typed helpers,
# and the default EDF/SDF report (macro F1 within each fluent_type bucket).
# ---------------------------------------------------------------------------


def get_micro(results: Iterable[ScoreResult]) -> ScoreResult:
    """Micro aggregate over scored predictions sharing one fluent type.

    Wraps ``compare.get_micro`` (sum TP/FP/FN, then derive P/R/F1). All inputs
    must share a ``fluent_type``; bucket by type first otherwise.
    """
    items = list(results)
    fluent_type = _single_fluent_type(items)
    micro = compare.get_micro(_as_per_unit(items))
    return ScoreResult(
        tp=int(micro["tp"]),
        fp=int(micro["fp"]),
        fn=int(micro["fn"]),
        precision=float(micro["precision"]),
        recall=float(micro["recall"]),
        f1=float(micro["f1"]),
        fluent_type=fluent_type,
    )


def get_macro(results: Iterable[ScoreResult]) -> ScoreResult:
    """Macro aggregate over scored predictions sharing one fluent type.

    Wraps ``compare.get_macro`` (unweighted mean of P/R/F1). TP/FP/FN are
    reported as the raw summed counts (``compare.get_macro`` itself returns a
    ``-1`` sentinel for those; we surface the real totals instead). All inputs
    must share a ``fluent_type``; bucket by type first otherwise.
    """
    items = list(results)
    fluent_type = _single_fluent_type(items)
    macro = compare.get_macro(_as_per_unit(items))
    return ScoreResult(
        tp=sum(sr.tp for sr in items),
        fp=sum(sr.fp for sr in items),
        fn=sum(sr.fn for sr in items),
        precision=float(macro["precision"]),
        recall=float(macro["recall"]),
        f1=float(macro["f1"]),
        fluent_type=fluent_type,
    )


def macro_f1_by_fluent_type(results: Iterable[ScoreResult]) -> dict[FluentType, float]:
    """Default report: macro F1 within each fluent-type bucket.

    Buckets the tagged scored predictions by ``fluent_type`` and returns the
    unweighted mean F1 per bucket (via :func:`get_macro`). Macro — not micro —
    because micro lets high-volume EDFs swamp the SDFs, hiding the central
    EDF-vs-SDF asymmetry the thesis measures (ARCHITECTURE.md §2).
    """
    buckets: dict[FluentType, list[ScoreResult]] = {}
    for sr in results:
        buckets.setdefault(sr.fluent_type, []).append(sr)
    return {fluent_type: get_macro(items).f1 for fluent_type, items in buckets.items()}


def _single_fluent_type(items: Sequence[ScoreResult]) -> FluentType:
    """Return the shared fluent type of ``items``; reject empty or mixed input."""
    if not items:
        raise ValueError("cannot aggregate an empty set of ScoreResults")
    types = {sr.fluent_type for sr in items}
    if len(types) != 1:
        raise ValueError(
            f"cannot aggregate scores spanning multiple fluent types {sorted(types)}; "
            "bucket by fluent_type first (e.g. macro_f1_by_fluent_type)"
        )
    return next(iter(types))


def _as_per_unit(items: Sequence[ScoreResult]) -> dict[int, dict[str, float]]:
    """Shape scored predictions into the per-unit dict ``compare`` reducers expect."""
    return {
        idx: {
            "tp": sr.tp,
            "fp": sr.fp,
            "fn": sr.fn,
            "precision": sr.precision,
            "recall": sr.recall,
            "f1": sr.f1,
        }
        for idx, sr in enumerate(items)
    }
