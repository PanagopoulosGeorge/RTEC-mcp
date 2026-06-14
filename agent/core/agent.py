"""ReAct Agent for RTEC rule generation."""

import json
import re
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
from .schemas import AgentState, AgentMessage, ToolCall, EvalReport, EvalSnapshotRecord
from .convergence import Convergence, Candidate, Status
from .feedback import to_feedback


def _fmt_eval(report: EvalReport) -> str:
    """Prepend a plain-text summary so the model reads the right F1 first."""
    header = f"OVERALL micro_f1={report.micro_f1:.4f}  macro_f1={report.macro_f1:.4f}\n"
    return header + report.model_dump_json()


# Different providers expose chain-of-thought under different field names.
_REASONING_FIELDS = ("reasoning_content", "reasoning")


def _reasoning_of(msg) -> str | None:
    """Surface chain-of-thought from whichever field the provider uses
    (`reasoning_content`: deepseek/moonshot; `reasoning`: OpenRouter/others),
    checking both direct attributes and the OpenAI SDK's `model_extra` catch-all.

    Only GPT-4o narrates in plain `content`, which is why it was previously the
    only model whose thinking was visible."""
    extra = getattr(msg, "model_extra", None) or {}
    for field in _REASONING_FIELDS:
        val = getattr(msg, field, None) or extra.get(field)
        if val:
            return val
    return None


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
        on_eval: Callable[[int, EvalReport, list[str] | None], None] | None = None,
        on_iteration: Callable[[int], None] | None = None,
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
        client_kwargs: dict = {}
        if self.config.api_key:
            client_kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url
        self.client = OpenAI(**client_kwargs)
        
        # Callbacks for observability
        self.on_thinking = on_thinking or (lambda x: None)
        self.on_tool_call = on_tool_call or (lambda n, a: None)
        self.on_tool_result = on_tool_result or (lambda n, r: None)
        self.on_eval = on_eval or (lambda i, r, f: None)
        self.on_iteration = on_iteration or (lambda i: None)
        
        # Tool dispatcher — only tools present in TOOL_DEFINITIONS.
        # run_rtec: excluded (compare_to_gold calls it internally).
        # Builder may only read its own output, never the expert answer key.
        self._tools = {
            "get_syntax_docs": lambda **_: get_syntax_docs(),
            "get_vocabulary": lambda app, **_: get_vocabulary(app).model_dump_json(),
            "compile_rules": lambda app, rules, **_: compile_rules(app, rules).model_dump_json(),
            "compare_to_gold": lambda app, fluents=None, **_: _fmt_eval(compare_to_gold(app, fluents)),
            "generate_gold": lambda app, **_: generate_gold(app),
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

        # Summarize run_rtec output if the tool is ever called via a legacy
        # path — raw intervals are too large for context.
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

    def _compile_nudge(self, compile_result: dict) -> str | None:
        """Return a nudge when compile_rules succeeds but has singleton warnings.

        Singleton variables almost always signal a predicate arity bug
        (e.g. areaType(AreaType) instead of areaType(Area, AreaType)) that
        silently makes the clause fail at runtime, producing F1=0 with no
        other visible error.
        """
        if not compile_result.get("success"):
            return None
        warnings = compile_result.get("warnings", [])
        if not warnings:
            return None
        singleton_vars: list[str] = []
        for w in warnings:
            m = re.search(r"Singleton variables: \[([^\]]+)\]", w)
            if m:
                singleton_vars.extend(v.strip() for v in m.group(1).split(","))
        if not singleton_vars:
            return None
        var_list = ", ".join(dict.fromkeys(singleton_vars))
        return (
            f"⚠ Compiler singleton warning: variable(s) [{var_list}] appear in only "
            "one predicate in their clause. You MUST fix ALL singleton warnings before "
            "calling compare_to_gold. There are two kinds — fix each appropriately:\n\n"
            "KIND 1 — Arity bug in a clause body (silent runtime failure): "
            "A variable bound by one literal is not passed to the next. "
            "Fix: use the correct predicate arity so the variable appears in both literals.\n\n"
            "KIND 2 — Unused pattern variable in an `index/2` or `grounding/1` fact "
            "Recompile after fixing all singletons, then call compare_to_gold."
        )

    def _record_eval(
        self,
        state: AgentState,
        iteration: int,
        eval_result: EvalReport,
        scoped_fluents: list[str] | None,
    ) -> None:
        previous_best = (
            state.eval_history[-1].best_so_far if state.eval_history else 0.0
        )
        micro = eval_result.micro_f1
        # Use micro_f1 per scoped fluent — matches the convergence metric and
        # avoids min(value-F1s) masking a mostly-working fluent as 0.
        if scoped_fluents:
            per_fluent = {f: eval_result.micro_f1 for f in scoped_fluents}
        else:
            by_fluent: dict[str, list[float]] = {}
            for s in eval_result.per_fluent:
                by_fluent.setdefault(s.fluent, []).append(s.f1)
            per_fluent = {f: min(vs) for f, vs in by_fluent.items()}

        snap = EvalSnapshotRecord(
            iteration=iteration,
            micro_f1=micro,
            macro_f1=eval_result.macro_f1,
            per_fluent_f1=per_fluent,
            scoped_fluents=scoped_fluents,
            delta=(micro - previous_best) if state.eval_history else None,
            best_so_far=max(previous_best, micro),
            improved=micro > previous_best + 1e-9,
        )
        state.eval_history.append(snap)
        state.last_eval = eval_result
        self.on_eval(iteration, eval_result, scoped_fluents)

    @staticmethod
    def _coalesce_system_messages(messages: list[dict]) -> list[dict]:
        """Merge all leading system messages into one.

        Llama-based models (e.g. via NVIDIA NIM) enforce a strict prompt
        template that allows exactly one system message at position 0.
        Sending multiple consecutive system messages causes a 500 error.
        """
        if not messages:
            return messages
        parts: list[str] = []
        idx = 0
        while idx < len(messages) and messages[idx].get("role") == "system":
            parts.append(messages[idx]["content"])
            idx += 1
        if len(parts) <= 1:
            return messages  # nothing to merge
        merged = {"role": "system", "content": "\n\n".join(parts)}
        return [merged] + messages[idx:]

    def _call_llm(self, messages: list[dict]) -> dict:
        """Call the LLM and return the response."""
        # Reasoning models (o1/o3/o4 series) use max_completion_tokens and do
        # not support temperature or system messages.
        is_reasoning = self.config.model.startswith(("o1", "o3", "o4"))
        # Non-OpenAI endpoints (NVIDIA NIM, etc.) enforce a single system
        # message at position 0 and don't accept tool_choice="auto".
        is_compat_endpoint = self.config.base_url is not None
        # NVIDIA NIM rejects tool_choice; most others (Groq, Ollama, Together)
        # support it fine. Omit it only for NIM.
        is_nvidia = self.config.base_url is not None and "nvidia" in (self.config.base_url or "")
        if is_compat_endpoint:
            messages = self._coalesce_system_messages(messages)
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
        }
        if not is_nvidia:
            kwargs["tool_choice"] = "auto"
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

        # Inject the domain vocabulary signature so the model knows all available
        # events, output fluents, entity domains, and threshold keys without
        # needing to call get_vocabulary() (which is not in TOOL_DEFINITIONS).
        sig_parts: list[str] = []
        if vocab.events:
            sig_parts.append(
                "Input events (use in happensAt):\n"
                + "\n".join(f"  {e}" for e in vocab.events)
            )
        if vocab.fluents:
            sig_parts.append(
                "Output fluents (unclassified — you decide simple vs SD from the NL request):\n"
                + "\n".join(f"  {f}" for f in vocab.fluents)
            )
        if vocab.entities:
            for etype, vals in vocab.entities.items():
                sig_parts.append(f"Entity domain '{etype}': {', '.join(str(v) for v in vals)}")
        if vocab.thresholds:
            sig_parts.append(
                "Named thresholds (bind with thresholds(key, Var) before use in arithmetic):\n"
                + "\n".join(f"  {k}" for k in vocab.thresholds)
            )
        if sig_parts:
            messages.append({
                "role": "system",
                "content": (
                    "Domain vocabulary (reference — call get_vocabulary for the full structured view):\n\n"
                    + "\n\n".join(sig_parts)
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
                    "your **first** compile_rules() call alongside any new rules, "
                    "because compile_rules() REPLACES the entire file.\n\n"
                    "If your new rules use holdsAt(F, ...) or holdsFor(F, ...), every "
                    "fluent F you reference must appear in that same compile payload — "
                    "including the rules below. Dropping them causes silent F1 failures "
                    "(e.g. all gap intervals classified as farFromPorts when withinArea "
                    "is missing).\n\n"
                    + existing
                ),
            })
        except Exception:
            pass  # No prior generated rules yet — nothing to preserve.

        messages.append({"role": "user", "content": user_message})

        state.messages.append(AgentMessage(role="user", content=user_message))
        
        last_compare_fluents: list[str] | None = None
        compiled_since_last_eval = False
        vocabulary_consulted = False
        vocab_nudge_sent = False
        silent_streak = 0  # consecutive iterations with no content and no tool calls

        # Convergence is the SINGLE OWNER of the terminal decision (CONVERGED /
        # EXHAUSTED / STALLED) and of return-best. The while below is only a
        # safety ceiling for the case where the model never calls compare_to_gold.
        conv = Convergence(
            tau=self.config.convergence_threshold,
            max_iters=self.config.max_iterations,
        )

        # ReAct loop
        while state.iteration < self.config.max_iterations:
            state.iteration += 1
            self.on_iteration(state.iteration)
            
            # Get LLM response
            response = self._call_llm(messages)

            # Surface reasoning-model chain-of-thought (lives in reasoning_content,
            # not content) so thinking is visible for more than just GPT-4o.
            reasoning = _reasoning_of(response)
            if reasoning:
                self.on_thinking(reasoning)
            elif self.config.debug and not response.content:
                # Diagnose a model that surfaces no thinking: show which extra
                # fields its message actually carries, so we know where (or whether)
                # reasoning lives for this provider.
                extra = getattr(response, "model_extra", None) or {}
                self.on_thinking(
                    "[debug] no content/reasoning surfaced this turn. "
                    f"message extra fields: {list(extra.keys()) or 'none'}"
                )

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
                silent_streak = 0
                tool_calls = []
                tool_messages = []
                compile_nudge: str | None = None

                for tc in response.tool_calls:
                    name = tc.function.name
                    args = json.loads(tc.function.arguments)
                    tool_calls.append(ToolCall(name=name, arguments=args))

                    # Execute tool
                    result = self._execute_tool(name, args)

                    if name == "compile_rules":
                        try:
                            payload = json.loads(result)
                            if payload.get("success"):
                                new_rules = args.get("rules")
                                # Unchanged-rule detector (structural, no leak): resubmitting
                                # byte-identical rules cannot change F1, so flag it and stop the
                                # model from spinning on the same candidate.
                                if new_rules is not None and new_rules == state.current_rules:
                                    compile_nudge = (
                                        "⚠ These rules are BYTE-IDENTICAL to your previous "
                                        "compile — re-evaluating them will return the exact same "
                                        "F1. Make a concrete change to the Prolog (different "
                                        "events, guards, or TERMINATION conditions) before "
                                        "compiling again; do not resubmit the same rules."
                                    )
                                compiled_since_last_eval = True
                                state.current_rules = new_rules
                            # nudge = self._compile_nudge(payload)
                            # if nudge:
                            #     compile_nudge = nudge
                        except Exception:
                            pass

                    # compare_to_gold: parse the report ONCE, then (a) redact it for
                    # the model (Gap 1 leak firewall) and (b) record it + feed
                    # Convergence (the single owner of the terminal decision).
                    content_for_model = result
                    if name == "compare_to_gold":
                        try:
                            eval_result = EvalReport.model_validate_json(
                                result[result.index("{"):]
                            )
                            content_for_model = to_feedback(eval_result)  # model view: redacted
                            scoped = args.get("fluents")
                            last_compare_fluents = scoped
                            compiled_since_last_eval = False
                            self._record_eval(state, state.iteration, eval_result, scoped)
                            # state.iteration is 1-indexed; Convergence is 0-indexed.
                            status = conv.update(Candidate(
                                rules=state.current_rules or "",
                                per_fluent_f1=eval_result.micro_f1,
                                macro_f1=eval_result.macro_f1,
                                iteration=state.iteration - 1,
                            ))
                            if status is not Status.RUNNING:
                                state.terminal_status = status.value
                                state.converged = status is Status.CONVERGED
                            # Debug: surface what the MODEL actually received + the
                            # convergence verdict (operator-only, never the model).
                            if self.config.debug:
                                self.on_tool_result("feedback → model", content_for_model)
                                self.on_tool_result(
                                    "convergence",
                                    f"status={status.value}  "
                                    f"best_f1={conv.best.per_fluent_f1:.3f}  {conv.detail}",
                                )
                        except Exception:
                            content_for_model = result

                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": content_for_model
                    })

                    state.messages.append(AgentMessage(
                        role="tool",
                        content=content_for_model,
                        tool_call_id=tc.id
                    ))

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

                # Add tool results, then any diagnostic nudges
                messages.extend(tool_messages)
                if compile_nudge and not state.converged:
                    messages.append({"role": "user", "content": compile_nudge})

                # Convergence is the single stop authority: break on ANY terminal
                # status (CONVERGED / EXHAUSTED / STALLED), not just success.
                if state.terminal_status is not None:
                    break
            else:
                # No tool calls - check if we should continue or stop
                # Don't exit early if F1 is still low and we have iterations left
                if state.last_eval is None or state.last_eval.micro_f1 < self.config.convergence_threshold:
                    silent_streak += 1
                    if silent_streak == 1:
                        nudge = (
                            "You must call compile_rules() to make progress. "
                            "IMPORTANT: Include ALL rules in one compile call — "
                            "rules for the target fluent AND every fluent it depends on. "
                            "Each compile_rules() REPLACES all previous rules."
                        )
                    else:
                        # Escalate: give a concrete example structure so the model
                        # has something to act on even if context is confusing.
                        f1_str = f"{state.last_eval.micro_f1:.3f}" if state.last_eval else "unknown"
                        nudge = (
                            f"[Nudge #{silent_streak}] Current F1={f1_str}. "
                            "You are NOT converged. Call compile_rules(app, rules) RIGHT NOW "
                            "with the complete rule set. Do not explain — just call the tool."
                        )
                    self.on_thinking(f"[no tool call — injecting nudge #{silent_streak}]")
                    messages.append({"role": "user", "content": nudge})
                    continue
                else:
                    # Agent has good F1, can stop
                    break

        # If the last compile was never scored, score it once so a potentially
        # best candidate isn't dropped (replaces the old, dead post-loop guard,
        # whose `iteration < max_iterations` condition was always false on exhaustion).
        if compiled_since_last_eval and state.current_rules and state.terminal_status is None:
            try:
                eval_result = compare_to_gold(app, last_compare_fluents)
                self._record_eval(
                    state, state.iteration, eval_result, last_compare_fluents
                )
                status = conv.update(Candidate(
                    rules=state.current_rules,
                    per_fluent_f1=eval_result.micro_f1,
                    macro_f1=eval_result.macro_f1,
                    iteration=state.iteration - 1,
                ))
                if status is not Status.RUNNING:
                    state.terminal_status = status.value
                    state.converged = status is Status.CONVERGED
            except Exception:
                pass

        # The loop may exit via the safety ceiling without conv declaring terminal
        # (e.g. the model never called compare_to_gold) -> that is EXHAUSTED.
        if state.terminal_status is None:
            state.terminal_status = Status.EXHAUSTED.value

        # Return-best: hand back the highest-F1 candidate, not whatever compiled
        # last. Recompile it so the on-disk artifact reflects the best, not the last.
        if conv.best.rules and conv.best.rules != state.current_rules:
            try:
                compile_rules(app, conv.best.rules)
                state.current_rules = conv.best.rules
            except Exception:
                pass
        
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
