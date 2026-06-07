"""ReAct Agent for RTEC rule generation."""

import json
from typing import Callable
from openai import OpenAI

from ..config import AgentConfig, PROMPTS_DIR
from ..tools import (
    TOOL_DEFINITIONS,
    compile_rules,
    run_rtec,
    compare_to_gold,
    generate_gold,
    get_vocabulary,
    get_syntax_docs,
    read_rules,
)
from .schemas import AgentState, AgentMessage, ToolCall, EvalReport


class RTECAgent:
    """
    ReAct agent for generating RTEC event descriptions.
    
    Implements the Think → Act → Observe loop with visible reasoning.
    """
    
    def __init__(
        self, 
        config: AgentConfig | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        on_tool_result: Callable[[str, str], None] | None = None,
    ):
        """
        Initialize the agent.
        
        Args:
            config: Agent configuration
            on_thinking: Callback when agent produces thinking
            on_tool_call: Callback when agent calls a tool
            on_tool_result: Callback when tool returns result
        """
        self.config = config or AgentConfig()
        self.client = OpenAI()
        
        # Callbacks for observability
        self.on_thinking = on_thinking or (lambda x: None)
        self.on_tool_call = on_tool_call or (lambda n, a: None)
        self.on_tool_result = on_tool_result or (lambda n, r: None)
        
        # Tool dispatcher
        self._tools = {
            "get_syntax_docs": lambda **_: get_syntax_docs(),
            "get_vocabulary": lambda app, **_: get_vocabulary(app).model_dump_json(),
            "compile_rules": lambda app, rules, **_: compile_rules(app, rules).model_dump_json(),
            "run_rtec": lambda app, **_: json.dumps([r.model_dump() for r in run_rtec(app)]),
            "compare_to_gold": lambda app, fluents=None, **_: compare_to_gold(app, fluents).model_dump_json(),
            "generate_gold": lambda app, **_: generate_gold(app),
            # Builder may only read its own output, never the expert answer key.
            "read_rules": lambda app, **_: read_rules(app, "generated"),
        }
    
    def _get_system_prompt(self, app: str) -> str:
        """Build system prompt for the agent."""
        system_file = PROMPTS_DIR / "system.md"
        if system_file.exists():
            return system_file.read_text().replace("{{APP}}", app)
        
        return f"""You are an expert RTEC (Run-Time Event Calculus) programmer. Your task is to generate event descriptions that correctly recognize complex events from input streams.

        ## Your Goal
        Generate RTEC rules for the "{app}" application that match the expected behavior (gold standard intervals).

        ## Workflow
        1. First, call get_syntax_docs() to understand RTEC syntax
        2. Then, call get_vocabulary("{app}") to see available events/fluents
        3. Generate rules using initiatedAt/terminatedAt for simple fluents, holdsFor for SD fluents
        4. Compile with compile_rules() to check syntax
        5. Run with run_rtec() and compare with compare_to_gold() to evaluate
        6. Iterate based on the behavioral feedback (missing/spurious intervals)

        ## Important Rules
        - Simple fluents use initiatedAt/terminatedAt (inertia: value persists until changed)
        - SD fluents use holdsFor with interval operations (union_all, intersect_all, etc.)
        - Always include grounding declarations for each fluent
        - Pay attention to false positives (rule too permissive) and false negatives (rule too restrictive)

        Think step by step, and explain your reasoning before each action."""
    
    def _execute_tool(self, name: str, arguments: dict) -> str:
        """Execute a tool and return the result."""
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
        """Call the LLM and return the response."""
        response = self.client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return response.choices[0].message
    
    def run(self, app: str, user_message: str) -> AgentState:
        """
        Run the agent on a task.
        
        Args:
            app: Application name
            user_message: User's request
            
        Returns:
            Final AgentState
        """
        state = AgentState(app=app)

        # Build initial messages
        messages = [
            {"role": "system", "content": self._get_system_prompt(app)},
        ]

        # Seed with any rules from previous runs so sequential requests
        # ACCUMULATE instead of overwriting. compile_rules() REPLACES the whole
        # file, and each run() is a cold start with no memory, so without this a
        # follow-up like "generate the fluent for location" would silently drop
        # the "rich" fluent generated in an earlier run.
        try:
            existing = read_rules(app, "generated")
            messages.append({
                "role": "system",
                "content": (
                    "The following rules already exist in generated_rules.prolog "
                    "from previous work. Unless the user explicitly asks you to "
                    "change or remove them, you MUST carry them over verbatim into "
                    "your next compile_rules() call alongside any new rules, because "
                    "compile_rules() REPLACES the entire file:\n\n" + existing
                ),
            })
        except Exception:
            pass  # No prior generated rules yet — nothing to preserve.

        messages.append({"role": "user", "content": user_message})

        state.messages.append(AgentMessage(role="user", content=user_message))
        
        # ReAct loop
        while state.iteration < self.config.max_iterations:
            state.iteration += 1
            
            # Get LLM response
            response = self._call_llm(messages)
            
            # Handle thinking/content
            if response.content:
                self.on_thinking(response.content)
                state.messages.append(AgentMessage(
                    role="assistant",
                    content=response.content,
                    thinking=response.content
                ))
                messages.append({"role": "assistant", "content": response.content})
            
            # Handle tool calls
            if response.tool_calls:
                tool_calls = []
                tool_messages = []
                
                for tc in response.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)
                    tool_calls.append(ToolCall(name=name, arguments=args))
                    
                    # Execute tool
                    result = self._execute_tool(name, args)
                    
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result
                    })
                    
                    state.messages.append(AgentMessage(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id
                    ))
                    
                    # Check for convergence
                    if name == "compare_to_gold":
                        try:
                            eval_result = EvalReport.model_validate_json(result)
                            state.last_eval = eval_result
                            if eval_result.micro_f1 >= self.config.convergence_threshold:
                                state.converged = True
                        except:
                            pass
                
                # Add assistant message with tool calls
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in response.tool_calls
                    ]
                })
                
                # Add tool results
                messages.extend(tool_messages)
                
                if state.converged:
                    break
            else:
                # No tool calls - check if we should continue or stop
                # Don't exit early if F1 is still low and we have iterations left
                if state.last_eval is None or state.last_eval.micro_f1 < self.config.convergence_threshold:
                    # Prompt the model to take action with specific guidance
                    nudge = (
                        "You must call compile_rules() to make progress. "
                        "IMPORTANT: Include ALL rules in one compile call - "
                        "rules for the target fluent AND every fluent it depends on. "
                        "Each compile_rules() REPLACES all previous rules."
                    )
                    messages.append({"role": "user", "content": nudge})
                    continue
                else:
                    # Agent has good F1, can stop
                    break
        
        return state
    
    def chat(self, app: str):
        """
        Start an interactive chat session.
        
        Args:
            app: Application name
        """
        print(f"\n🤖 RTEC Agent - Working on '{app}'")
        print("=" * 50)
        print("Type your request, or 'quit' to exit.\n")
        
        while True:
            try:
                user_input = input("You: ").strip()
                if user_input.lower() in ('quit', 'exit', 'q'):
                    break
                if not user_input:
                    continue
                
                print("\n" + "-" * 50)
                state = self.run(app, user_input)
                
                print("-" * 50)
                if state.converged:
                    print(f"✅ Converged! F1 = {state.last_eval.micro_f1:.3f}")
                elif state.last_eval:
                    print(f"📊 Current F1 = {state.last_eval.micro_f1:.3f}")
                print()
                
            except KeyboardInterrupt:
                print("\nExiting...")
                break
