"""EnginePort — abstract RTEC executor.

Implementations live in ``rtec_llm/adapters/engine/``. The default adapter wraps
``execution scripts/run_rtec.sh`` (see ``docs/ENGINE_NOTES.md`` §3 for the
seam analysis). The engine itself is treated as read-only and is the *sole*
correctness signal for rule behaviour (CLAUDE.md §3 hard invariant 1 + §5
hard invariant 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from rtec_llm.types import ExecutionResult, Window


@runtime_checkable
class EnginePort(Protocol):
    """Abstract single-run RTEC executor.

    Implementations MUST:

    * Treat ``execution scripts/`` and ``src/`` as read-only (CLAUDE.md §5).
    * Materialise ``rules`` to a temp Prolog file if needed; do not mutate
      example rule files in place.
    * Re-compile rules before running if the underlying entry point requires it
      (e.g. ``run_rtec.sh`` compiles automatically; the Python CLI does not —
      see ``docs/ENGINE_NOTES.md`` §3.2).
    * Surface engine compile / runtime errors via ``ExecutionResult.errors``
      rather than raising — the repair loop reacts to those errors as
      diagnostic signal.
    """

    def run(
        self,
        *,
        rules: str,
        declarations: str | None,
        event_stream: Path,
        window: Window,
        static_data: tuple[Path, ...],
    ) -> ExecutionResult:
        """Run RTEC once over the given event stream and window.

        Args:
            rules: Prolog event description (initiatedAt / terminatedAt /
                holdsFor clauses). The adapter is free to write this to a temp
                file before invoking the engine.
            declarations: Optional grounding declarations and other compiler
                hints. If ``None``, the adapter assumes everything is in
                ``rules``. Grounding is deterministic per CLAUDE.md §5
                invariant 4 — this argument exists for adapters that want to
                separate the two for clarity, not to let the LLM author them.
            event_stream: Path to the input CSV (pipe-delimited, see
                ``docs/ENGINE_NOTES.md`` §5.4 for the column layout).
            window: Temporal window parameters (start, end, size, step).
            static_data: Background knowledge files (thresholds, vocabulary
                tables, helper predicates). Order is preserved; the adapter
                consults them in the given order.

        Returns:
            ``ExecutionResult`` with grouped recognised intervals and any
            engine errors. A successful run has empty ``errors`` and a
            populated ``recognised``; a failed compile has populated
            ``errors`` and empty ``recognised``.
        """
        ...
