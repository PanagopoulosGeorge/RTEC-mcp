"""Free-form QA agent for RTEC apps.

Unlike RTECAgent (which is locked into the compile -> run -> compare
convergence loop), this agent answers questions about an existing event
description. It uses only read-only tools and stops as soon as it has an
answer — there is no F1 target and no forced action nudge.
"""

import json
from typing import Callable

from openai import OpenAI

from ..config import AgentConfig, PROMPTS_DIR
from ..tools import (
    QA_TOOL_DEFINITIONS,
    get_syntax_docs,
    get_vocabulary,
    read_rules,
    run_rtec,
)


def _recognize(app: str, source: str = "expert") -> str:
    """Run RTEC and serialize the recognised intervals as JSON."""
    recs = run_rtec(app, use_generated=(source == "generated"))
    return json.dumps([r.model_dump() for r in recs])


class QAAgent:
    """ReAct agent that answers questions about an RTEC app.

    Same Think -> Act -> Observe shape as RTECAgent, but the loop simply ends
    when the model returns a message with no tool calls (i.e. its answer).
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
    ):
        self.config = config or AgentConfig()
        self.client = OpenAI()

        self.on_thinking = on_thinking or (lambda x: None)
        self.on_tool_call = on_tool_call or (lambda n, a: None)
        self.on_tool_result = on_tool_result or (lambda n, r: None)

        # Read-only tool dispatcher.
        self._tools = {
            "get_syntax_docs": lambda **_: get_syntax_docs(),
            "get_vocabulary": lambda app, **_: get_vocabulary(app).model_dump_json(),
            "read_rules": lambda app, source="expert", **_: read_rules(app, source),
            "recognize": lambda app, source="expert", **_: _recognize(app, source),
        }

    def _get_system_prompt(self, app: str) -> str:
        qa_file = PROMPTS_DIR / "qa_system.md"
        if qa_file.exists():
            return qa_file.read_text().replace("{{APP}}", app)
        return (
            f"You are an RTEC expert assistant for the '{app}' application. "
            "Answer the user's questions about the existing event description. "
            "Use read_rules to inspect rules and recognize to get the time "
            "intervals during which fluents hold. Ground every claim in a tool "
            "result; do not invent intervals."
        )

    def _execute_tool(self, name: str, arguments: dict) -> str:
        self.on_tool_call(name, arguments)
        if name not in self._tools:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        else:
            try:
                result = self._tools[name](**arguments)
                if not isinstance(result, str):
                    result = json.dumps(result)
            except Exception as e:
                result = json.dumps({"error": str(e)})
        self.on_tool_result(name, result)
        return result

    def _call_llm(self, messages: list[dict]) -> dict:
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=QA_TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return response.choices[0].message

    def ask(self, app: str, question: str, history: list[dict] | None = None) -> str:
        """Answer a question about an app.

        Args:
            app: Application name.
            question: The user's question.
            history: Optional prior messages (OpenAI format) to continue a chat.

        Returns:
            The agent's final answer text.
        """
        if history is None:
            messages = [{"role": "system", "content": self._get_system_prompt(app)}]
        else:
            messages = history
        messages.append({"role": "user", "content": question})

        answer = ""
        iterations = 0
        while iterations < self.config.max_iterations:
            iterations += 1
            response = self._call_llm(messages)

            # No tool calls -> this is the final answer.
            if not response.tool_calls:
                answer = response.content or ""
                if answer:
                    self.on_thinking(answer)
                messages.append({"role": "assistant", "content": answer})
                break

            # Record the assistant turn (content may accompany tool calls).
            if response.content:
                self.on_thinking(response.content)
            messages.append({
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response.tool_calls
                ],
            })

            for tc in response.tool_calls:
                args = json.loads(tc.function.arguments)
                result = self._execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return answer

    def chat(self, app: str):
        """Interactive QA session that keeps conversation history."""
        print(f"\n💬 RTEC QA - Ask about '{app}'")
        print("=" * 50)
        print("Type your question, or 'quit' to exit.\n")

        history = [{"role": "system", "content": self._get_system_prompt(app)}]
        while True:
            try:
                user_input = input("You: ").strip()
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                if not user_input:
                    continue
                answer = self.ask(app, user_input, history=history)
                print(f"\nAgent: {answer}\n")
            except KeyboardInterrupt:
                print("\nExiting...")
                break
