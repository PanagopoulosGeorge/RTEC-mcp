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
    # Optional domain examples shown to the stage-1 parser (NL descriptions of
    # how key fluents behave). Keyed by a short label, value is a free-text
    # description. Populated from vocabulary.yaml's `patterns` block.
    patterns: dict[str, str] = Field(default_factory=dict)
    # Representative NL requests for this domain (not used by the parser itself,
    # but useful for evaluation and documentation).
    example_requests: list[str] = Field(default_factory=list)


# ============= Stage 1: NL -> typed spec (the IR) =============

class EventRef(BaseModel):
    """A reference to an input event, e.g. win_lottery(X)."""
    event: str
    args: list[str] = Field(default_factory=list)


class ConditionRef(BaseModel):
    """A (fluent=value) condition evaluated over intervals, e.g. location(X)=pub."""
    fluent: str
    args: list[str] = Field(default_factory=list)
    value: str = "true"


# How NL connectives map to RTEC interval operations.
_OP_WORD = {
    "union": "union",              # "or" / "either" over durative conditions
    "intersect": "intersection",   # "and" / "while both"
    "complement": "relative complement",  # "but not" / "except when"
}


class DefinitionExpr(BaseModel):
    """An interval expression defining an SD fluent.

    For `complement`, the first operand is the base set and the rest are
    subtracted from it (matches RTEC `relative_complement_all`).
    """
    op: Literal["union", "intersect", "complement"]
    operands: list[ConditionRef] = Field(default_factory=list)


class FluentSpec(BaseModel):
    """Typed, signature-grounded specification of a single fluent.

    This is the intermediate representation between natural language (stage 1
    input) and Prolog rules (stage 2 output). It deliberately encodes *what*
    the fluent means in terms of known vocabulary symbols, not *how* RTEC
    expresses it — the builder agent owns the Prolog.
    """
    target: str
    args: list[str] = Field(default_factory=list)
    kind: Literal["simple_fluent", "sd_fluent"]
    value: str = "true"
    # SD fluent: derived from other intervals.
    definition: DefinitionExpr | None = None
    # Simple fluent: event-triggered initiation/termination (inertia).
    initiated_by: list[EventRef] = Field(default_factory=list)
    terminated_by: list[EventRef] = Field(default_factory=list)

    def to_brief(self) -> str:
        """Render the spec as an unambiguous instruction for the builder agent."""
        head = f"{self.target}({', '.join(self.args)})={self.value}"
        if self.kind == "sd_fluent" and self.definition:
            word = _OP_WORD[self.definition.op]
            lines = [
                f"Generate RTEC rules for the statically-determined fluent {head}.",
                f"It holds during the {word} of:",
            ]
            for c in self.definition.operands:
                cond = f"{c.fluent}({', '.join(c.args)})={c.value}"
                lines.append(f"  - intervals where {cond}")
            lines.append(
                "Define every dependency fluent it references (if not already "
                "present) and include grounding declarations for all fluents."
            )
            return "\n".join(lines)

        lines = [f"Generate RTEC rules for the simple fluent {head}."]
        for e in self.initiated_by:
            lines.append(f"It is initiated when {e.event}({', '.join(e.args)}) happens.")
        for e in self.terminated_by:
            ev = f"{e.event}({', '.join(e.args)})"
            lines.append(f"It is terminated when {ev} happens.")
        lines.append("Include grounding declarations.")
        return "\n".join(lines)


class TranslationResult(BaseModel):
    """Output of stage 1: a spec plus how well it grounds in the signature."""
    spec: FluentSpec | None = None
    valid: bool = False
    errors: list[str] = Field(default_factory=list)    # hard grounding failures
    warnings: list[str] = Field(default_factory=list)  # soft notes
    brief: str = ""  # rendered instruction passed to the builder

    def summary(self) -> str:
        lines = []
        if self.spec:
            lines.append(self.brief)
        if self.errors:
            lines.append("\nGrounding errors:")
            lines.extend(f"  ✗ {e}" for e in self.errors)
        if self.warnings:
            lines.append("\nWarnings:")
            lines.extend(f"  ! {w}" for w in self.warnings)
        return "\n".join(lines) or "(no spec)"


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
