"""Unit tests for the RTEC scoring adapter — engine-independent (no swipl).

These exercise the oracle's wrapper directly: the point-set timepoint F1 is the
*only* correctness signal in this codebase (CLAUDE.md §3), so its adaptation
layer must be tested against hand-computed half-open interval arithmetic, not a
compile pass. No subprocess, no ground-truth files beyond tiny temp fixtures.

Covered:
* exact / disjoint / partial-overlap F1 (hand-computed, half-open ``[s, e)``);
* off-by-one endpoint drift → expected F1 + ``boundary_off_by_one`` flags;
* the typed→dict prediction adaptation (predicted side; never touches parser.py);
* ground-truth file parser robustness on a nested-bracket recognitions line.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import rtec_llm.adapters.scoring.rtec_scoring as rs
from rtec_llm.adapters.scoring import RtecScorer
from rtec_llm.ports.scoring import ScoringPort
from rtec_llm.types import (
    ExecutionResult,
    FluentValuePair,
    Interval,
    RecognisedFluent,
    ScoreResult,
)

_FVP_NAME = "withinArea"
_FVP_VALUE = "true"
_ARGS = ("209273000", "fishing")


def _prediction(intervals: Iterable[tuple[int, int]]) -> ExecutionResult:
    """A one-FVP ExecutionResult, as the engine adapter would hand to the scorer."""
    return ExecutionResult(
        recognised=(
            RecognisedFluent(
                fluent=FluentValuePair(_FVP_NAME, _FVP_VALUE),
                args=_ARGS,
                intervals=tuple(Interval(s, e) for s, e in intervals),
            ),
        )
    )


def _gt_line(intervals: Iterable[tuple[int, int]]) -> str:
    iv = ",".join(f"({s},{e})" for s, e in intervals)
    args = ",".join(_ARGS)
    return f"recognitions(predictions,{_FVP_NAME},[[{args}],{_FVP_VALUE}],[{iv}])."


def _gt_file(tmp_path: Path, intervals: Iterable[tuple[int, int]]) -> Path:
    path = tmp_path / "gt.txt"
    path.write_text(_gt_line(intervals) + "\n", encoding="utf-8")
    return path


def test_adapter_satisfies_scoring_port() -> None:
    """Structural check: the adapter is a valid ScoringPort implementation."""
    assert isinstance(RtecScorer(), ScoringPort)


def test_exact_match_scores_one(tmp_path: Path) -> None:
    """Identical predicted and ground-truth intervals → perfect score, no disagreements."""
    result = RtecScorer().score(_prediction([(0, 10)]), _gt_file(tmp_path, [(0, 10)]), "EDF")

    assert (result.tp, result.fp, result.fn) == (10, 0, 0)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.fluent_type == "EDF"
    assert result.disagreements == ()


def test_disjoint_scores_zero(tmp_path: Path) -> None:
    """Non-overlapping intervals → F1 0.0; sampler flags pure over-/under-production."""
    result = RtecScorer().score(_prediction([(20, 30)]), _gt_file(tmp_path, [(0, 10)]), "SDF")

    assert (result.tp, result.fp, result.fn) == (0, 10, 10)
    assert result.f1 == 0.0
    assert result.fluent_type == "SDF"
    kinds = {(d.timestamp, d.kind) for d in result.disagreements}
    assert kinds == {(0, "under_production"), (20, "over_production")}


def test_partial_overlap_handcomputed(tmp_path: Path) -> None:
    """Half-open overlap of [5,10): tp=5, fp=5, fn=5 → F1 = 0.5.

    The 5-tick diverging tails abut the matched region but exceed the off-by-one
    tolerance, so they are production, not ``boundary_off_by_one``.
    """
    result = RtecScorer().score(_prediction([(5, 15)]), _gt_file(tmp_path, [(0, 10)]), "EDF")

    assert (result.tp, result.fp, result.fn) == (5, 5, 5)
    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.f1 == 0.5
    assert {d.kind for d in result.disagreements} == {"over_production", "under_production"}


def test_off_by_one_endpoints_flagged(tmp_path: Path) -> None:
    """Each predicted interval ends one tick late across three intervals.

    GT timepoints = 30, predicted = 33, tp = 30, fp = 3, fn = 0 → F1 = 60/63.
    Every 1-tick tail abuts a match end, so all three are ``boundary_off_by_one``.
    """
    gt = [(0, 10), (20, 30), (40, 50)]
    pred = [(0, 11), (20, 31), (40, 51)]
    result = RtecScorer().score(_prediction(pred), _gt_file(tmp_path, gt), "SDF")

    assert (result.tp, result.fp, result.fn) == (30, 3, 0)
    assert result.f1 == 60 / 63
    assert {d.kind for d in result.disagreements} == {"boundary_off_by_one"}
    assert {d.timestamp for d in result.disagreements} == {10, 30, 50}


def test_typed_to_dict_adaptation() -> None:
    """Predicted RecognisedFluent → compare_ce dict shape (Interval → [s, e]).

    Predicted side only: this is a typed→dict transform, never a parse of RTEC
    output (parser-ownership contract). Args/value keys must match the shape
    parser.parse_file produces for the ground truth, so the two sides line up.
    """
    recognised = (
        RecognisedFluent(
            fluent=FluentValuePair("withinArea", "true"),
            args=("209273000", "fishing"),
            intervals=(Interval(100, 200),),
        ),
    )

    assert rs._to_compare_dict(recognised) == {
        ("withinArea", "true"): {("209273000", "fishing"): [[100, 200]]}
    }


def test_ground_truth_parser_robustness_on_nested_brackets(tmp_path: Path) -> None:
    """parser.parse_file keys a nested-bracket recognitions line correctly.

    The GT line ``recognitions(predictions,withinArea,[[209273000,fishing],true],
    [(100,200)]).`` must split into FVP ``("withinArea","true")`` → args
    ``("209273000","fishing")`` → ``[[100,200]]`` without mis-tokenising the
    nested ``],`` brackets. It does, so no shim is needed in the adapter.
    """
    path = tmp_path / "gt.txt"
    path.write_text(
        "recognitions(predictions,withinArea,[[209273000,fishing],true],[(100,200)]).\n",
        encoding="utf-8",
    )

    parsed = rs.parser.parse_file(path)

    assert parsed == {("withinArea", "true"): {("209273000", "fishing"): [[100, 200]]}}


def test_macro_f1_by_fluent_type_buckets_by_regime() -> None:
    """The default report means F1 within each regime bucket, EDF and SDF apart."""
    edf_a = ScoreResult(tp=8, fp=2, fn=0, precision=0.8, recall=1.0, f1=0.8, fluent_type="EDF")
    edf_b = ScoreResult(tp=10, fp=0, fn=0, precision=1.0, recall=1.0, f1=1.0, fluent_type="EDF")
    sdf = ScoreResult(tp=1, fp=4, fn=5, precision=0.2, recall=0.166, f1=0.2, fluent_type="SDF")

    report = rs.macro_f1_by_fluent_type([edf_a, edf_b, sdf])

    assert report["EDF"] == (0.8 + 1.0) / 2
    assert report["SDF"] == 0.2
