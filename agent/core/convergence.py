"""
Terminal-status convergence controller (Course M5).

One threshold (tau), two gates:
  - loop gate   = the targeted fluent's F1 >= tau      -> CONVERGED
  - report gate = macro-F1 over all fluents (owned by the caller, for the final claim)

Status is an enum, never a bool. Only CONVERGED is convergence; EXHAUSTED (under-resourced) and
STALLED (unsynthesizable by this agent) are distinct non-convergence terminations.

Return-best, not return-last: F1 is non-monotonic, so the best candidate is checkpointed.
"""

from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    RUNNING = "running"
    CONVERGED = "converged"      # per-fluent F1 >= tau
    EXHAUSTED = "exhausted"      # hit max-iters (record still-climbing)
    STALLED = "stalled"          # best-F1 plateaued on the verdict


@dataclass
class Candidate:
    rules: str
    per_fluent_f1: float         # loop-gate metric (score of the targeted fluent)
    macro_f1: float              # carried for the final report gate
    iteration: int


class Convergence:
    def __init__(self, tau: float = 0.95, max_iters: int = 20,
                 patience: int = 4, delta: float = 0.01):
        self.tau = tau
        self.max_iters = max_iters
        self.patience = patience
        self.delta = delta
        self.best = Candidate(rules="", per_fluent_f1=0.0, macro_f1=0.0, iteration=0)
        self.trajectory: list[float] = []
        self.since_improve = 0
        self.detail = ""

    def update(self, cand: Candidate) -> Status:
        """Feed one verified candidate; return the (possibly terminal) status."""
        self.trajectory.append(cand.per_fluent_f1)

        # return-best + patience: "new best" and "improved by >= delta" are different tests.
        compared_to_best = cand.per_fluent_f1 - self.best.per_fluent_f1
        if compared_to_best > 0:
            self.best = cand
        if compared_to_best > self.delta:
            self.since_improve = 0
        else:
            self.since_improve += 1

        # precedence: success > stalled > exhausted
        if cand.per_fluent_f1 >= self.tau:
            self.detail = f"per-fluent F1={cand.per_fluent_f1:.3f} >= tau={self.tau}"
            return Status.CONVERGED
        if self.since_improve >= self.patience:
            self.detail = (f"best F1={self.best.per_fluent_f1:.3f} flat for "
                           f">= {self.patience} iters (stalled)")
            return Status.STALLED
        if cand.iteration + 1 >= self.max_iters:
            climbing = self.since_improve == 0
            self.detail = (f"max_iters={self.max_iters} hit, best={self.best.per_fluent_f1:.3f}, "
                           f"{'still climbing' if climbing else 'flat'} at cutoff")
            return Status.EXHAUSTED
        self.detail = "running"
        return Status.RUNNING
