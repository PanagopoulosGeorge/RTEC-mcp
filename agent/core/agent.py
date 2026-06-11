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

        # Summarize run_rtec output — raw intervals are too large for context.
        # The model should use compare_to_gold for evaluation instead.
        if name == "run_rtec":
            try:
                recs = json.loads(result)
                if isinstance(recs, list):
                    counts: dict[str, int] = {}
                    for r in recs:
                        k = r.get("fluent", "?")
                        counts[k] = counts.get(k, 0) + 1
                    result = json.dumps({
                        "total_recognitions": len(recs),
                        "per_fluent_count": counts,
                        "note": (
                            "Raw intervals omitted to save context. "
                            "Call compare_to_gold for F1 scores and diffs."
                        ),
                    })
            except Exception:
                pass

        self.on_tool_result(name, result)
        return result
    
    def _eval_nudge(
        self, current: EvalReport, previous: EvalReport | None
    ) -> str | None:
        """Return a targeted nudge message based on the evaluation result.

        Fires when F1 is stuck (no improvement) OR when the fp/fn ratio is
        extreme enough that the failure mode is unambiguous.  Returns None
        when no specific advice can be given or when F1 already converged.
        """
        if current.micro_f1 >= self.config.convergence_threshold:
            return None

        total_tp = sum(s.tp for s in current.per_fluent)
        total_fp = sum(s.fp for s in current.per_fluent)
        total_fn = sum(s.fn for s in current.per_fluent)

        stuck = (
            previous is not None
            and abs(current.micro_f1 - previous.micro_f1) < 0.001
        )
        # Ratios are only meaningful when either side is non-zero
        fp_dominated = total_fp > 10 * max(total_fn, 1)
        fn_dominated = total_fn > 10 * max(total_fp, 1)
        nothing_fires = total_tp == 0 and total_fp == 0

        lines: list[str] = []

        if stuck:
            lines.append(
                "⚠ F1 has not changed since the last iteration — your rules "
                "are identical. You MUST make a concrete change to the Prolog "
                "before calling compile_rules() again."
            )

        if nothing_fires:
            lines.append(
                "The fluent NEVER fires (tp=0, fp=0, fn>0). "
                "Your initiatedAt condition is never satisfied. "
                "Verify that every symbol you reference — event names, "
                "area type values, threshold keys — exactly matches what "
                "get_vocabulary() returns. A single wrong atom silently "
                "makes the clause fail every time."
            )
        elif fp_dominated:
            lines.append(
                f"Recall is perfect but precision is very low "
                f"(fp={total_fp:,}, fn={total_fn:,}). "
                "The fluent holds for too long — the bug is in your "
                "TERMINATION conditions, not initiation. "
                "Common mistake: leavesArea(Vessel, X) never fires when X is "
                "an area type name — leavesArea takes a specific area polygon "
                "ID, not a type name. "
                "To terminate when a vessel leaves an area that is tracked as "
                "a fluent F=V, use the built-in derived event instead: "
                "happensAt(end(F=V), T). "
                "For example, to terminate when a vessel leaves the nearCoast "
                "zone: happensAt(end(withinArea(Vessel, nearCoast)=true), T). "
                "The end(F=V) event fires at each endpoint of every maximal "
                "interval of F=V (documented in the syntax docs)."
            )
        elif fn_dominated:
            lines.append(
                f"Precision is high but recall is very low "
                f"(fp={total_fp:,}, fn={total_fn:,}). "
                "The fluent fires far less often than it should — your "
                "INITIATION condition is too strict. "
                "Try relaxing or removing a guard, or verify that all "
                "referenced threshold keys and entity values are correct "
                "by calling get_vocabulary()."
            )

        return "\n".join(lines) if lines else None

    def _call_llm(self, messages: list[dict]) -> dict:
        """Call the LLM and return the response."""
        # Reasoning models (o1/o3/o4 series) use max_completion_tokens and do
        # not support temperature or system messages.
        is_reasoning = self.config.model.startswith(("o1", "o3", "o4"))
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
        }
        if is_reasoning:
            kwargs["max_completion_tokens"] = self.config.max_tokens
        else:
            kwargs["max_tokens"] = self.config.max_tokens
            kwargs["temperature"] = self.config.temperature
        response = self.client.chat.completions.create(**kwargs)
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

        vocab = get_vocabulary(app)

        # Inject the required preamble — always at the top of every compile call.
        if vocab.preamble:
            messages.append({
                "role": "system",
                "content": (
                    "Every compile_rules() call MUST start with this preamble "
                    "verbatim (without it RTEC cannot resolve vessel/1 and will "
                    "crash):\n\n"
                    f"```prolog\n{vocab.preamble.strip()}\n```"
                ),
            })

        # Inject background knowledge predicate descriptions so the agent knows
        # exactly which Prolog facts are available at runtime and what they mean.
        if vocab.background_predicates:
            messages.append({
                "role": "system",
                "content": (
                    "The following background knowledge predicates are asserted "
                    "at runtime and can be called freely in your rule bodies. "
                    "Each entry shows the Prolog signature and its meaning — use "
                    "these to look up thresholds, vessel types, area mappings, "
                    "and interval utilities:\n\n"
                    + "\n".join(f"  {p}" for p in vocab.background_predicates)
                ),
            })

        # Inject few-shot examples from vocabulary.yaml as reference material.
        # Each example has three fields: nl (the request), explain (clause-by-
        # clause reasoning), and rule (only the rules for that fluent).
        if vocab.examples:
            example_sections = []
            for ex in vocab.examples:
                section = (
                    f"NL request: {ex.nl.strip()}\n\n"
                    f"Explanation: {ex.explain.strip()}\n\n"
                    f"Rule:\n```prolog\n{ex.rule.strip()}\n```"
                )
                example_sections.append(section)
            messages.append({
                "role": "system",
                "content": (
                    "The following are worked examples from the domain document. "
                    "Study the NL request, the clause-by-clause explanation, and "
                    "the rule to understand correct RTEC style for this domain.\n\n"
                    "DEPENDENCY RULE — you MUST follow this:\n"
                    "If your generated rule body contains holdsAt(F=V, T) or "
                    "holdsFor(F=V, I) where F is an output fluent (e.g. withinArea, "
                    "gap, stopped, lowSpeed, movingSpeed), you MUST include F's "
                    "complete rule block from the examples above in your "
                    "compile_rules() call alongside your target rules. "
                    "Omitting a dependency means F is undefined at runtime and "
                    "every holdsAt/holdsFor query on it silently returns false — "
                    "this produces F1=0 for all values that depend on it. "
                    "Only include fluents your target actually references; "
                    "do NOT include unrelated fluents.\n\n"
                    + "\n\n---\n\n".join(example_sections)
                ),
            })

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
                post_nudge: str | None = None

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

                    # Check for convergence and compute diagnostic nudge
                    if name == "compare_to_gold":
                        try:
                            eval_result = EvalReport.model_validate_json(result)
                            nudge = self._eval_nudge(eval_result, state.last_eval)
                            state.last_eval = eval_result
                            if eval_result.micro_f1 >= self.config.convergence_threshold:
                                state.converged = True
                            elif nudge:
                                post_nudge = nudge
                        except Exception:
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

                # Add tool results, then any diagnostic nudge
                messages.extend(tool_messages)
                if post_nudge and not state.converged:
                    messages.append({"role": "user", "content": post_nudge})

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
