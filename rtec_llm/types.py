"""Shared types — the lingua franca between ports, adapters, and core logic.

Keep this module narrow and free of business logic. Every type here is either
named by the P1 brief or is an internal helper required to express one of those
types.

All dataclasses are frozen + slotted to make them hashable, cheap, and safe to
pass through the LangGraph RepairState (see CLAUDE.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Primitive aliases
# ---------------------------------------------------------------------------

type Timestamp = int
"""Engine timestamp. Maritime uses UNIX epoch seconds; toy/caviar use tick counts."""

type Role = Literal["system", "user", "assistant"]
"""Chat-message role. Tool / tool-result roles are deferred to a later phase if needed."""

FluentType = Literal["EDF", "SDF"]
"""Event regime tag for a fluent.

* ``EDF`` — event-driven fluent, defined by ``initiatedAt`` / ``terminatedAt``.
* ``SDF`` — statically determined fluent, defined by a single ``holdsFor`` rule
  whose body combines other fluents via interval algebra
  (``union_all``/``intersect_all``/``relative_complement_all``/``intDurGreater``).

The engine has no notion of this distinction. It is a thesis-level label
carried alongside every score so the evaluation harness can slice F1 by regime
(the central thesis measurement — see CLAUDE.md §3 and §5 hard invariant 7).
"""

DisagreementKind = Literal["boundary_off_by_one", "over_production", "under_production"]
"""Structural classification of a single predicted-vs-ground-truth disagreement.

* ``boundary_off_by_one`` — a ≈1-tick region immediately adjacent to a matched
  region: the right-open off-by-one drift (effects take hold at ``T+1``, so an
  endpoint lands one tick off across every interval — CLAUDE.md §3 failure mode 4).
* ``over_production`` — a substantial region predicted but absent from ground
  truth (a false-positive span that is *not* a boundary artefact).
* ``under_production`` — a substantial region present in ground truth but not
  predicted (a false-negative span that is *not* a boundary artefact).

This is a *structural* (interval-geometry) label produced by the scoring
adapter's disagreement sampler. The repair orchestrator maps it onto a
*semantic* cause class (boundary comparator / wrong termination / missing
branch) when building the next repair instruction.
"""


# ---------------------------------------------------------------------------
# Interval primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    """One chat-style message in an LLM message list.

    Kept minimal: role + text content. Streaming, tool calls, and multimodal
    parts are intentionally out of scope at this layer — the LLM adapter
    converts to/from provider-native shapes if needed.
    """

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open temporal interval ``[start, end)``.

    Matches RTEC's right-open interval convention (CLAUDE.md §3 failure mode 4).
    A point-set timepoint count is therefore ``end - start``.
    """

    start: Timestamp
    end: Timestamp


@dataclass(frozen=True, slots=True)
class Window:
    """RTEC execution window parameters.

    Mirrors the per-app fields in ``execution scripts/defaults.toml``.
    Step may equal ``window_size`` (non-overlapping) or be smaller (overlapping,
    used to accommodate delayed events).
    """

    start_time: Timestamp
    end_time: Timestamp
    window_size: int
    step: int


# ---------------------------------------------------------------------------
# Recognised intervals
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FluentValuePair:
    """Identifies a fluent regardless of grounded args.

    Example: ``FluentValuePair("withinArea", "true")``. This is the key the
    existing scoring layer uses to group results (see
    ``execution scripts/scoring/utilities/parser.py``).
    """

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class RecognisedFluent:
    """Intervals during which one grounded (fluent, value, args) triple holds."""

    fluent: FluentValuePair
    args: tuple[str, ...]
    intervals: tuple[Interval, ...]


# ---------------------------------------------------------------------------
# Engine result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EngineError:
    """A single error reported by the engine adapter.

    Errors do NOT raise out of an ``EnginePort.run`` call. They propagate via
    ``ExecutionResult.errors`` so the repair orchestrator can react to them as
    diagnostic signal for the next iteration.
    """

    kind: str
    """Coarse category, e.g. ``compile_error``, ``runtime_error``, ``timeout``."""

    message: str
    """Free-form text — typically the engine's stderr line(s)."""

    source: str | None = None
    """Optional location pointer, e.g. ``rules.prolog:42`` if parseable."""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Result of one ``EnginePort.run`` invocation.

    ``recognised`` is empty if the engine failed; ``errors`` is empty on a
    successful run. Both being non-empty is legal — RTEC can emit warnings
    alongside results.
    """

    recognised: tuple[RecognisedFluent, ...]
    errors: tuple[EngineError, ...] = field(default_factory=tuple)
    wall_time_ms: int | None = None


# ---------------------------------------------------------------------------
# Scoring result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One sampled timestamp where prediction and ground truth diverge.

    Produced by the scoring adapter's disagreement sampler. ``timestamp`` is the
    first timepoint of the diverging region; ``kind`` is its structural class
    (see :data:`DisagreementKind`). A bounded sample of these is the
    highest-information signal the repair orchestrator gets for the next
    iteration — vague "try again" feedback is forbidden (CLAUDE.md §3).
    """

    timestamp: Timestamp
    kind: DisagreementKind


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Point-set timepoint scores for one prediction (or a bucket aggregate).

    The metric is defined in
    ``execution scripts/scoring/utilities/compare.py``: timepoints are summed
    over half-open intervals (``end - start``); precision/recall/F1 are derived
    from TP/FP/FN counts. ``tp`` / ``fp`` / ``fn`` are the *raw* timepoint counts
    summed across the prediction's fluent-value pairs.

    ``disagreements`` is the bounded, classified sample of timestamps where
    predicted and ground-truth differ. The orchestrator maps each structural
    class onto a semantic cause class (boundary comparator / wrong termination /
    missing branch) and feeds that as the next repair instruction.
    """

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    fluent_type: FluentType
    disagreements: tuple[Disagreement, ...] = field(default_factory=tuple)


__all__ = [
    "Disagreement",
    "DisagreementKind",
    "EngineError",
    "ExecutionResult",
    "FluentType",
    "FluentValuePair",
    "Interval",
    "Message",
    "RecognisedFluent",
    "Role",
    "ScoreResult",
    "Timestamp",
    "Window",
]
