"""Unified interactive session that routes between the two agents.

The builder (RTECAgent) generates/modifies rules and loops to F1 convergence;
the QA agent (QAAgent) answers read-only questions about existing rules. They
cannot be one agent (opposite stop policies + different toolsets), so this
session holds both and routes each user turn to one of them.

Routing is automatic (a cheap LLM intent classifier) with a manual override:
a message starting with `/build` or `/ask` forces that agent. The two agents
share state only through generated_rules.prolog on disk — the builder writes
it, the QA agent reads it.
"""

from dataclasses import dataclass
from typing import Callable

from openai import OpenAI

from ..config import AgentConfig
from .agent import RTECAgent
from .qa_agent import QAAgent
from .schemas import AgentState


_ROUTER_PROMPT = (
    "You route a user's message to one of two RTEC assistants. Reply with "
    "exactly one word, no punctuation:\n"
    "- 'build' if they want to create, modify, add, fix, or regenerate rules / "
    "fluents (anything that changes the event description).\n"
    "- 'ask' if they want to understand existing rules, or know WHEN/WHY a "
    "fluent holds (a read-only question).\n"
    "When unsure, answer 'ask'."
)

# Fallback keywords if the classifier call fails.
_BUILD_KEYWORDS = (
    "generate", "create", "build", "add ", "define", "implement",
    "fix", "rewrite", "modify", "change the rule", "compile", "regenerate",
)


@dataclass
class TurnResult:
    """Outcome of one routed turn."""
    route: str            # 'build' | 'ask'
    forced: bool          # True if routed by an explicit /build or /ask
    answer: str | None = None      # set when route == 'ask'
    state: AgentState | None = None  # set when route == 'build'
    fluent_key: str | None = None  # vocabulary pattern key, if resolved
    payload: str = ""


class RouterSession:
    """Holds both agents and dispatches each user message to one of them."""

    def __init__(
        self,
        app: str,
        config: AgentConfig | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
        on_eval: Callable | None = None,
        on_iteration: Callable[[int], None] | None = None,
        on_build_start: Callable[[str, str | None], None] | None = None,
        resolve_request: Callable[[str, str], tuple[str, bool]] | None = None,
    ):
        self.app = app
        self.config = config or AgentConfig()
        self.client = OpenAI()
        self._resolve_request = resolve_request
        self._on_build_start = on_build_start

        cb = dict(
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_eval=on_eval,
            on_iteration=on_iteration,
        )
        self.builder = RTECAgent(config=self.config, **cb)
        self.qa = QAAgent(
            config=self.config,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )

        # QA keeps a running conversation; the builder is stateless (file-seeded).
        self.qa_history = [
            {"role": "system", "content": self.qa._get_system_prompt(app)}
        ]

    # ---- routing ---------------------------------------------------------

    @staticmethod
    def _parse_override(text: str) -> tuple[str | None, str]:
        """Return (forced_route, payload). forced_route is None if no override."""
        t = text.strip()
        if t.startswith("/"):
            first, _, rest = t.partition(" ")
            cmd = first[1:].lower()
            if cmd in ("build", "b"):
                return "build", rest.strip()
            if cmd in ("ask", "qa", "q"):
                return "ask", rest.strip()
        return None, text

    def _heuristic(self, text: str) -> str:
        low = text.lower()
        return "build" if any(k in low for k in _BUILD_KEYWORDS) else "ask"

    def _classify(self, text: str) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": _ROUTER_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
                max_tokens=4,
            )
            out = (resp.choices[0].message.content or "").strip().lower()
            if "build" in out:
                return "build"
            if "ask" in out:
                return "ask"
        except Exception:
            pass
        return self._heuristic(text)

    # ---- dispatch --------------------------------------------------------

    def _resolve_fluent_key(self, text: str) -> str | None:
        if not self._resolve_request:
            return None
        candidate = text.strip().split()[0] if text.strip() else ""
        if not candidate:
            return None
        _, was_lookup = self._resolve_request(self.app, candidate)
        return candidate if was_lookup else None

    def dispatch(self, text: str) -> TurnResult:
        """Route one user message and run the chosen agent."""
        route, payload = self._parse_override(text)
        forced = route is not None
        if route is None:
            route = self._classify(text)
            payload = text

        fluent_key = self._resolve_fluent_key(payload) if route == "build" else None

        if route == "build":
            if self._on_build_start:
                self._on_build_start(payload, fluent_key)
            state = self.builder.run(self.app, payload)
            return TurnResult(
                route="build",
                forced=forced,
                state=state,
                fluent_key=fluent_key,
                payload=payload,
            )

        answer = self.qa.ask(self.app, payload, history=self.qa_history)
        return TurnResult(
            route="ask",
            forced=forced,
            answer=answer,
            payload=payload,
        )
