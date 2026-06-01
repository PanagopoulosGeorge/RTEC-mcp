"""Port interfaces — Protocols that adapters implement and core logic depends on.

Dependency direction: adapters → ports → types. Core logic (generation, repair,
query, evaluation) depends only on ports, never on a concrete adapter. See
``docs/ARCHITECTURE.md``.
"""

from rtec_llm.ports.engine import EnginePort
from rtec_llm.ports.llm import LLMPort
from rtec_llm.ports.scoring import ScoringPort

__all__ = ["EnginePort", "LLMPort", "ScoringPort"]
