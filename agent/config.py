"""Configuration for the RTEC ReAct agent."""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal

# Project paths
REPO_ROOT = Path(__file__).parent.parent
AGENT_ROOT = Path(__file__).parent
APPS_DIR = AGENT_ROOT / "apps"
PROMPTS_DIR = AGENT_ROOT / "prompts"

# RTEC paths
RTEC_SRC = REPO_ROOT / "src"
RTEC_COMPILER = RTEC_SRC / "compiler.prolog"
RTEC_SCRIPTS = REPO_ROOT / "execution scripts"
RTEC_EXAMPLES = REPO_ROOT / "examples"


@dataclass
class AgentConfig:
    """Configuration for agent behavior."""
    
    # LLM settings
    model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 4096
    
    # ReAct loop settings
    max_iterations: int = 20
    convergence_threshold: float = 0.95  # F1 score
    
    # Verbosity
    show_thinking: bool = True
    show_tool_calls: bool = True
    show_tool_results: bool = True


@dataclass
class AppConfig:
    """Configuration for a registered application."""
    
    name: str
    path: Path
    
    # RTEC execution parameters
    window_size: int = 10
    step: int = 10
    start_time: int = 0
    end_time: int = 100
    clock_tick: int = 1
    
    # Paths (relative to app path)
    expert_rules: str = "expert_rules.prolog"
    input_stream: str = "input_stream.csv"
    gold_intervals: str = "gold_intervals.txt"
    vocabulary: str = "vocabulary.yaml"
    generated_rules: str = "generated_rules.prolog"
    
    @property
    def expert_rules_path(self) -> Path:
        return self.path / self.expert_rules
    
    @property
    def input_stream_path(self) -> Path:
        return self.path / self.input_stream
    
    @property
    def gold_intervals_path(self) -> Path:
        return self.path / self.gold_intervals
    
    @property
    def vocabulary_path(self) -> Path:
        return self.path / self.vocabulary
    
    @property
    def generated_rules_path(self) -> Path:
        return self.path / self.generated_rules
