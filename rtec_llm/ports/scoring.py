"""ScoringPort — abstract correctness oracle.

Implementations live in ``rtec_llm/adapters/scoring/``. The default adapter
wraps ``execution scripts/scoring/utilities/`` (see ``docs/ENGINE_NOTES.md``
§4 and the "Scoring runs" section of ``CLAUDE.md``).

F1 from this port is *the* correctness signal for the repair loop
(CLAUDE.md §3). Never substitute LLM self-confidence, a compile check, or a
heuristic for the score returned here.

**Parser-ownership contract (ARCHITECTURE.md §7 P3 / §8 P2-2).** Predicted
intervals arrive already typed — the caller passes the ``ExecutionResult`` from
the engine adapter, which is the *sole* parser of RTEC's predicted output. The
ground-truth file is the only thing this layer parses (via
``scoring/utilities/parser.parse_file``). There is exactly one parse path for
predictions; the scoring layer must never re-parse an RTEC result file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from rtec_llm.types import ExecutionResult, FluentType, ScoreResult


@runtime_checkable
class ScoringPort(Protocol):
    """Abstract point-set timepoint F1 scorer.

    Implementations MUST:

    * Compute scores in *timepoints*, not intervals — half-open
      ``[start, end)`` semantics summing to ``end - start`` per interval. This
      matches the existing utilities and RTEC's interval convention.
    * Adapt the typed ``predicted`` intervals to the scoring utilities' shape
      in memory (a typed→dict transform, never a re-parse of RTEC output) and
      parse *only* the ground-truth file.
    * Treat ``fluent_type`` as a pass-through label only — the regime tag is
      carried onto the result for EDF/SDF slicing, but never affects the metric.
    * Populate ``ScoreResult.disagreements`` with a bounded, classified sample
      the orchestrator can map to a cause class for the next repair iteration.
    """

    def score(
        self,
        predicted: ExecutionResult,
        ground_truth_file: Path,
        fluent_type: FluentType,
    ) -> ScoreResult:
        """Compute point-set precision / recall / F1 for one prediction.

        Args:
            predicted: The typed ``ExecutionResult`` from the engine adapter —
                already-parsed recognised intervals across every grounded
                fluent-value pair the rule produced. Adapted in memory to the
                scoring utilities' dict shape; never re-parsed from a file.
            ground_truth_file: Path to a raw RTEC ``recognitions(...)`` file of
                the annotated reference intervals. The *only* file this layer
                parses, via ``parser.parse_file``. MUST use RTEC's half-open
                ``[s, e)`` convention or every F1 is systematically wrong.
            fluent_type: EDF or SDF tag (from the fixture) carried through to
                the result so the evaluation harness can slice scores by regime.
                Does NOT affect the metric calculation.

        Returns:
            A single ``ScoreResult`` aggregating the prediction's fluent-value
            pairs: raw timepoint TP / FP / FN, precision / recall / F1, the
            ``fluent_type`` tag, and the bounded classified disagreement sample.

        Notes:
            Cross-fluent EDF-vs-SDF reporting is a separate concern: collect the
            per-prediction ``ScoreResult``s and roll them up with the adapter's
            ``macro_f1_by_fluent_type`` helper (macro within each regime bucket,
            so high-volume EDFs do not swamp SDFs).
        """
        ...
