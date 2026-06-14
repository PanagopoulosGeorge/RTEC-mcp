"""Pydantic schemas for the ReAct agent."""

from pydantic import BaseModel, Field
from typing import Literal


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


class FluentExample(BaseModel):
    """A worked example from the domain document (llms.docx).

    `nl`      : the composite activity description as given to the LLM.
    `explain` : step-by-step reasoning explaining each clause choice.
    `rule`    : only the rules for this fluent + grounding/index — no
                dependency fluent rules and no simpleFluent/inputEntity/
                outputEntity declarations (the compiler infers those).
    """
    nl: str
    explain: str = ""
    rule: str


class Vocabulary(BaseModel):
    """Available vocabulary for an application.

    The signature is intentionally **flat**: events are listed separately, but
    fluents are a single unclassified list. The agent decides whether each
    fluent is simple or statically determined from the natural language request
    — we do NOT pre-classify them in the signature, because that would leak
    the answer the agent is supposed to derive.
    """
    events: list[str] = Field(default_factory=list)
    # Flat list of every fluent symbol the domain exposes. The order or
    # grouping carries no semantics — do not infer "simple" vs "SD" from it.
    fluents: list[str] = Field(default_factory=list)
    entities: dict[str, list[str]] = Field(default_factory=dict)  # type -> values
    thresholds: dict[str, int | float | None] = Field(default_factory=dict)
    # Required Prolog boilerplate that must appear at the top of every
    # compile_rules() call (collectIntervals, dynamicDomain, needsGrounding,
    # buildFromPoints). Domain-specific; populated from vocabulary.yaml.
    preamble: str = ""
    # Background knowledge predicates available at runtime. Each entry is a
    # self-contained description: "predicate(args) — meaning". Populated from
    # vocabulary.yaml's `background_predicates` block.
    background_predicates: list[str] = Field(default_factory=list)
    # NL descriptions of each output fluent, keyed by fluent name.
    # Populated from vocabulary.yaml's `patterns` block. Passed verbatim to
    # the agent as the task description.
    patterns: dict[str, str] = Field(default_factory=dict)
    # Representative NL requests for this domain (documentation only).
    example_requests: list[str] = Field(default_factory=list)
    # Few-shot NL + rules pairs. `nl` surfaces as domain reference context;
    # `rules` are pre-seeded into the agent as correct definitions to build on.
    examples: list[FluentExample] = Field(default_factory=list)


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

class EvalSnapshotRecord(BaseModel):
    """One F1 measurement during a build run (for observability)."""
    iteration: int
    micro_f1: float
    macro_f1: float
    per_fluent_f1: dict[str, float] = Field(default_factory=dict)
    scoped_fluents: list[str] | None = None
    delta: float | None = None
    best_so_far: float = 0.0
    improved: bool = False


class AgentState(BaseModel):
    """Current state of the ReAct agent."""
    app: str
    iteration: int = 0
    messages: list[AgentMessage] = Field(default_factory=list)
    current_rules: str | None = None
    last_eval: EvalReport | None = None
    eval_history: list[EvalSnapshotRecord] = Field(default_factory=list)
    converged: bool = False
    terminal_status: str | None = None  # CONVERGED / EXHAUSTED / STALLED
