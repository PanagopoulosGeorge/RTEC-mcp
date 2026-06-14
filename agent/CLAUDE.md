# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

This directory (`agent/`) is a self-contained Python subproject layered on top of the RTEC reasoner. It is a **ReAct agent that generates RTEC event descriptions (Prolog rules) from natural language**, then closes the loop by compiling, running, and behaviorally evaluating those rules against a gold standard until the F1 score converges. The reasoner it drives is documented in the repo-root `../CLAUDE.md` — read that for how RTEC itself compiles and runs event descriptions, the entity type system (events / simple fluents / SD fluents), and the example-app directory convention. This file covers only the agent.

## Commands

All commands run from the **repo root** (`..`), because the package is imported as `agent.*` and `config.py` derives every RTEC path relative to the repo root (`REPO_ROOT = agent/..`).

```bash
source ../.venv/bin/activate          # or repo-root .venv
uv pip install -e "agent/[dev]"       # installs openai, click, rich, pydantic, pyyaml (+ pytest)

python -m agent.cli apps              # list registered apps (= subdirs of agent/apps/)
python -m agent.cli vocab toy         # show events/fluents/entities for an app
python -m agent.cli syntax            # dump the RTEC grammar reference (prompts/syntax.md)
python -m agent.cli gold toy          # generate gold_intervals.txt from expert_rules (run ONCE per app)
python -m agent.cli chat toy          # interactive ReAct session
python -m agent.cli run toy "A person is happy as long as they are rich or at the pub"        # NL -> Prolog rules via the ReAct loop
```

`chat`/`run` take `--model` (default `gpt-4o`) and `--max-iter` (default 10). **`OPENAI_API_KEY` must be set** — `RTECAgent` constructs a bare `OpenAI()` client. The LLM backend is OpenAI chat-completions function-calling, *not* the Anthropic API.

There is no automated test suite here: `agent/tests/` currently contains only stale `__pycache__` (the former `demo_chat`/`demo_maritime` scripts are gone). `[dev]` pulls in pytest, but there are no `test_*.py` files to run yet.

## Architecture: the ReAct convergence loop

The whole system turns a natural-language request directly into Prolog rules that reproduce the behavior of a hidden `expert_rules.prolog`, measured by interval-level F1. There is no intermediate representation: the raw NL (or the pattern description from `vocabulary.yaml`) is passed straight to the ReAct loop. The loop lives in `core/agent.py::RTECAgent.run()`:

1. **System prompt** (`prompts/system.md`, `{{APP}}` substituted) primes the model with RTEC concepts and hard behavioral constraints (see "Prompt invariants" below).
2. **Think → Act → Observe.** Each iteration calls OpenAI with `TOOL_DEFINITIONS` and `tool_choice="auto"`. Tool calls are dispatched through `self._tools` (a dict of lambdas wrapping the real tool functions; results are serialized back as JSON strings into the message history).
3. **Convergence detection.** Whenever the model calls `compare_to_gold`, the result is parsed into an `EvalReport`; if `micro_f1 >= config.convergence_threshold` (0.95), `state.converged = True` and the loop breaks.
4. **No-action nudge.** If the model replies with prose but no tool call and F1 is still below threshold, the loop injects a hard-coded user message telling it to call `compile_rules` with the *complete* rule set, then continues. This is the main guard against the agent stalling.

The six tools (defined as OpenAI function schemas in `tools/__init__.py::TOOL_DEFINITIONS`, implemented across `tools/*.py`):

| Tool | File | What it actually does |
|------|------|------------------------|
| `get_syntax_docs()` | `registry.py` | Returns `prompts/syntax.md` (falls back to an embedded default). |
| `get_vocabulary(app)` | `registry.py` | Reads `apps/<app>/vocabulary.yaml`; if absent, regex-scrapes events/fluents/entities out of `expert_rules.prolog`. |
| `compile_rules(app, rules)` | `compile.py` | Writes `rules` to a temp file in the app dir, shells out to `swipl -l src/compiler.prolog -g "compileED(File, withoutOptimisation)"`, scrapes stdout/stderr for ERROR/Warning lines. On success, renames the temp source → `generated_rules.prolog` and the compiler's `compiled_rules.prolog` → `generated_rules_compiled.prolog`. |
| `run_rtec(app)` | `execute.py` | Shells out to `swipl -l "execution scripts/continuousQueries.prolog" -g "continuousQueries(app, [params])"` with `cwd=REPO_ROOT`, using `generated_rules_compiled.prolog` + `apps/<app>/auxiliary/*.prolog` against `input_stream.csv`. Parses `results/*recognised-intervals.txt`. |
| `compare_to_gold(app)` | `evaluate.py` | Runs the generated rules, diffs intervals against `gold_intervals.txt` per `(fluent, value, args)` using pure-Python temporal intersection/difference, returns micro/macro F1 + per-fluent FP/FN diffs. |
| `generate_gold(app)` | `evaluate.py` | Runs `run_rtec(use_generated=False)` against `expert_rules` and writes the result as `gold_intervals.txt`. |

`run_rtec(use_generated=...)` is the shared execution primitive: `True` → `generated_rules*`, `False` → `expert_rules*` (used to mint the gold standard). It prefers the `*_compiled.prolog` variant and falls back to the source `.prolog`.

All Pydantic models (`CompileResult`, `Recognition`, `EvalReport`, `Vocabulary`, `AgentState`, etc.) live in `core/schemas.py`. Tool results cross the agent boundary as `.model_dump_json()` strings.

## App convention (agent-specific — differs from the reasoner's `examples/` layout)

Apps live flat under `agent/apps/<name>/` (toy, voting, maritime), **not** in the nested `resources/`+`dataset/` structure that `../examples/<app>/` uses. A complete app directory:

```
agent/apps/<name>/
  config.yaml              # window_size, step, start_time, end_time, clock_tick (+ free-text description)
  vocabulary.yaml          # SIGNATURE ONLY: events / fluents (unclassified) / entities / patterns (no per-fluent definitions)
  expert_rules.prolog      # ground-truth rules; source of the gold standard
  expert_rules_compiled.prolog
  input_stream.csv         # the event stream fed to RTEC
  gold_intervals.txt       # generated by `gold` command from expert_rules
  auxiliary/*.prolog       # background knowledge (entity domains, compare predicates) consulted at run time
  generated_rules.prolog            # written by compile_rules (the agent's output)
  generated_rules_compiled.prolog
  results/                 # RTEC run logs + recognised-intervals files
```

`config.yaml` keys map directly onto `AppConfig` dataclass fields and onto the RTEC `continuousQueries` params; missing → defaults in `config.py`/`execute.py` (window 10, step 10, start 0, end 100). Adding an app = creating this directory with at least `expert_rules.prolog`, `input_stream.csv`, and (ideally) `vocabulary.yaml`, then running `python -m agent.cli gold <name>`. The app name is **not** validated against an enum here (unlike the reasoner's `RTEC2` CLI) — any subdir of `apps/` is a valid app.

## Prompt invariants you must preserve

`prompts/system.md` encodes hard-won constraints about how the RTEC compiler and this loop behave. If you edit prompts or the loop, do not break these — they exist because the model otherwise stalls or produces F1=0:

- **`compile_rules()` REPLACES the entire rule set each call.** The model must emit *all* fluent definitions (target SD fluent + every simple fluent it transitively references via `holdsFor`) plus *all* `grounding/1` declarations in a single call. Partial compiles silently drop previously-defined fluents. The no-action nudge in `agent.py` reinforces this.
- **SD fluents reference other fluents through `holdsFor` and have no inertia** — they are *not* defined with `holdsAt`. Simple fluents need both `initiatedAt` and `terminatedAt`. These mirror the reasoner's type system (see `../CLAUDE.md`).
- The agent is instructed to **iterate autonomously until F1 ≥ 0.95** and never stop to ask the user — convergence is purely behavioral (interval F1 vs. gold), not syntactic.
