"""Pydantic schemas for the ReAct agent."""

from pydantic import BaseModel, Field
from typing import Literal
from enum import Enum


# ============= Tool Input/Output Schemas =============

class CompileResult(BaseModel):
    """Result of compiling RTEC rules."""
    success: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Recognition(BaseModel):
    """A single RTEC recognition."""
    fluent: str
    args: list[str]
    value: str
    intervals: list[tuple[int, int]]


class IntervalDiff(BaseModel):
    """Difference between gold and generated intervals."""
    fluent: str
    value: str
    false_positives: list[tuple[int, int]] = Field(default_factory=list)
    false_negatives: list[tuple[int, int]] = Field(default_factory=list)


class FluentScore(BaseModel):
    """Evaluation score for a single fluent-value pair."""
    fluent: str
    value: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0


class EvalReport(BaseModel):
    """Full evaluation report."""
    micro_f1: float
    macro_f1: float
    per_fluent: list[FluentScore] = Field(default_factory=list)
    diffs: list[IntervalDiff] = Field(default_factory=list)
    
    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"Overall F1: {self.micro_f1:.3f} (micro), {self.macro_f1:.3f} (macro)"]
        if self.diffs:
            lines.append("\nProblematic fluents:")
            for d in self.diffs[:5]:  # Top 5
                fp_count = len(d.false_positives)
                fn_count = len(d.false_negatives)
                if fp_count or fn_count:
                    lines.append(f"  {d.fluent}={d.value}: {fp_count} spurious, {fn_count} missing")
        return "\n".join(lines)


class Vocabulary(BaseModel):
    """Available vocabulary for an application."""
    events: list[str] = Field(default_factory=list)
    simple_fluents: list[str] = Field(default_factory=list)
    sd_fluents: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(default_factory=dict)  # type -> values
    thresholds: dict[str, int | float] = Field(default_factory=dict)


# ============= Agent Message Schemas =============

class ToolCall(BaseModel):
    """A tool call made by the agent."""
    name: str
    arguments: dict


class AgentMessage(BaseModel):
    """A message in the agent conversation."""
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    thinking: str | None = None  # Chain-of-thought (if visible)


# ============= ReAct State =============

class AgentState(BaseModel):
    """Current state of the ReAct agent."""
    app: str
    iteration: int = 0
    messages: list[AgentMessage] = Field(default_factory=list)
    current_rules: str | None = None
    last_eval: EvalReport | None = None
    converged: bool = False
