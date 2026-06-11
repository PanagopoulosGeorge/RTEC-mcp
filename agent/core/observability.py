"""Session observability: F1 history, fluent progress, monotonic improvement."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

from .schemas import EvalReport, FluentScore


def _aggregate_fluent_f1(scores: list[FluentScore]) -> dict[str, float]:
    """Worst-case F1 per fluent name (min across value variants)."""
    by_fluent: dict[str, list[float]] = {}
    for s in scores:
        by_fluent.setdefault(s.fluent, []).append(s.f1)
    return {f: min(vs) for f, vs in by_fluent.items()}


@dataclass
class EvalSnapshot:
    """One compare_to_gold observation during a build run."""

    iteration: int
    micro_f1: float
    macro_f1: float
    per_fluent_f1: dict[str, float]
    scoped_fluents: list[str] | None = None
    delta: float | None = None
    best_so_far: float = 0.0
    improved: bool = False

    @classmethod
    def from_report(
        cls,
        iteration: int,
        report: EvalReport,
        scoped_fluents: list[str] | None,
        previous_best: float,
    ) -> EvalSnapshot:
        micro = report.micro_f1
        improved = micro > previous_best + 1e-9
        delta = micro - previous_best if previous_best > 0 else None
        per_fluent_f1 = _aggregate_fluent_f1(report.per_fluent)
        # When all scoped fluents are perfect, per_fluent is empty —
        # fill in with micro_f1 so the snapshot carries the data.
        if not per_fluent_f1 and scoped_fluents and micro > 0:
            per_fluent_f1 = {f: micro for f in scoped_fluents}
        return cls(
            iteration=iteration,
            micro_f1=micro,
            macro_f1=report.macro_f1,
            per_fluent_f1=per_fluent_f1,
            scoped_fluents=scoped_fluents,
            delta=(micro - previous_best) if previous_best >= 0 else None,
            best_so_far=max(previous_best, micro),
            improved=improved,
        )


class FluentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASS = "pass"
    FAIL = "fail"


@dataclass
class FluentProgress:
    name: str
    status: FluentStatus = FluentStatus.PENDING
    best_f1: float = 0.0
    iterations: int = 0
    last_run_at: datetime | None = None
    eval_points: list["FluentEvalRecord"] = field(default_factory=list)


@dataclass
class FluentEvalRecord:
    """One F1 observation for a fluent (session-wide history row)."""

    fluent: str
    run_number: int
    iteration: int
    f1: float
    delta: float | None = None

    @property
    def arrow(self) -> str:
        if self.delta is None:
            return "—"
        if self.delta > 1e-9:
            return "↑"
        if self.delta < -1e-9:
            return "↓"
        return "→"


@dataclass
class ChatEntry:
    kind: Literal["user", "assistant", "thinking", "tool_call", "tool_result", "system"]
    text: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BuildRun:
    """One builder invocation (single user request)."""

    request: str
    fluent_key: str | None = None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    converged: bool = False
    snapshots: list[EvalSnapshot] = field(default_factory=list)

    @property
    def best_f1(self) -> float:
        if not self.snapshots:
            return 0.0
        return max(s.micro_f1 for s in self.snapshots)

    @property
    def monotonic(self) -> bool:
        """True if micro_f1 never decreased across eval snapshots."""
        if len(self.snapshots) < 2:
            return True
        peak = 0.0
        for s in self.snapshots:
            if s.micro_f1 < peak - 1e-9:
                return False
            peak = max(peak, s.micro_f1)
        return True


_FLUENT_COMMENT = re.compile(r"^%\s*[─\-]+\s*(.+?)\s*[─\-]+\s*$", re.MULTILINE)


def fluents_in_rules(rules_text: str) -> list[str]:
    """Extract fluent section names from generated_rules.prolog comments."""
    return _FLUENT_COMMENT.findall(rules_text)


@dataclass
class SessionTracker:
    """Accumulates metrics across an interactive session."""

    app: str
    fluent_catalog: list[str] = field(default_factory=list)
    fluent_progress: dict[str, FluentProgress] = field(default_factory=dict)
    chat: list[ChatEntry] = field(default_factory=list)
    runs: list[BuildRun] = field(default_factory=list)
    current_run: BuildRun | None = None
    busy: bool = False
    eval_history: list[FluentEvalRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in self.fluent_catalog:
            self.fluent_progress.setdefault(name, FluentProgress(name=name))

    def add_chat(self, kind: Literal["user", "assistant", "thinking", "tool_call", "tool_result", "system"], text: str) -> None:
        self.chat.append(ChatEntry(kind=kind, text=text.strip()))

    def start_build(self, request: str, fluent_key: str | None = None) -> BuildRun:
        run = BuildRun(request=request, fluent_key=fluent_key)
        self.current_run = run
        self.runs.append(run)
        self.busy = True
        if fluent_key and fluent_key in self.fluent_progress:
            self.fluent_progress[fluent_key].status = FluentStatus.IN_PROGRESS
        return run

    def finish_build(self, converged: bool, fluent_key: str | None = None) -> None:
        if self.current_run:
            self.current_run.converged = converged
            self.current_run.finished_at = datetime.now()
            key = fluent_key or self.current_run.fluent_key
            if key and key in self.fluent_progress:
                prog = self.fluent_progress[key]
                prog.best_f1 = max(prog.best_f1, self.current_run.best_f1)
                prog.iterations = len(prog.eval_points)
                prog.last_run_at = datetime.now()
                if converged and self.current_run.best_f1 >= 0.95:
                    prog.status = FluentStatus.PASS
                elif self.current_run.snapshots:
                    prog.status = FluentStatus.FAIL
        self.current_run = None
        self.busy = False

    def _run_number(self) -> int:
        if not self.current_run or not self.runs:
            return len(self.runs)
        try:
            return self.runs.index(self.current_run) + 1
        except ValueError:
            return len(self.runs)

    def _previous_f1_in_run(self, fluent: str, run_number: int) -> float | None:
        for rec in reversed(self.eval_history):
            if rec.fluent == fluent and rec.run_number == run_number:
                return rec.f1
        return None

    def record_eval(
        self,
        iteration: int,
        report: EvalReport,
        scoped_fluents: list[str] | None,
    ) -> EvalSnapshot:
        if not self.current_run:
            self.current_run = BuildRun(request="(unknown)")
            self.runs.append(self.current_run)

        previous_best = (
            self.current_run.snapshots[-1].best_so_far
            if self.current_run.snapshots
            else 0.0
        )
        snap = EvalSnapshot.from_report(
            iteration, report, scoped_fluents, previous_best
        )
        self.current_run.snapshots.append(snap)

        # When the evaluation is scoped to specific fluents, use micro_f1 as
        # the single-number summary for each fluent.  micro_f1 is the actual
        # convergence metric (TP-weighted across all values), so it's what the
        # dashboard should display — not min(value-F1s), which shows 0.000 even
        # when farFromPorts=0.94 and only nearPorts=0.0 is missing.
        run_number = self._run_number()
        if scoped_fluents:
            per_fluent_f1 = {f: snap.micro_f1 for f in scoped_fluents}
        else:
            per_fluent_f1 = snap.per_fluent_f1

        for fname, f1 in per_fluent_f1.items():
            prev = self._previous_f1_in_run(fname, run_number)
            delta = (f1 - prev) if prev is not None else None
            rec = FluentEvalRecord(
                fluent=fname,
                run_number=run_number,
                iteration=iteration,
                f1=f1,
                delta=delta,
            )
            self.eval_history.append(rec)
            if fname in self.fluent_progress:
                self.fluent_progress[fname].eval_points.append(rec)
                prog = self.fluent_progress[fname]
                prog.best_f1 = max(prog.best_f1, f1)
                if f1 >= 0.95:
                    prog.status = FluentStatus.PASS
        return snap

    def mark_generated_fluents(self, rules_text: str) -> None:
        for name in fluents_in_rules(rules_text):
            if name in self.fluent_progress:
                if self.fluent_progress[name].status == FluentStatus.PENDING:
                    self.fluent_progress[name].status = FluentStatus.IN_PROGRESS

    @property
    def pass_count(self) -> int:
        return sum(1 for p in self.fluent_progress.values() if p.status == FluentStatus.PASS)

    @property
    def total_fluents(self) -> int:
        return len(self.fluent_catalog)

    def fluents_with_history(self) -> list[str]:
        """Fluent names that appear in eval history, catalog order first."""
        seen = {r.fluent for r in self.eval_history}
        ordered = [f for f in self.fluent_catalog if f in seen]
        for r in self.eval_history:
            if r.fluent not in ordered:
                ordered.append(r.fluent)
        return ordered
