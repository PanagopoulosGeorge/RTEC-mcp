# RTEC ReAct Agent

A reasoning agent that generates RTEC event descriptions from natural language.

## Architecture

```
agent/
├── tools/           # RTEC tool implementations
│   ├── __init__.py
│   ├── compile.py   # compile_rules()
│   ├── execute.py   # run_rtec()
│   ├── evaluate.py  # compare_to_gold()
│   └── registry.py  # app management
├── prompts/         # System prompts and few-shot examples
│   ├── system.md    # Main system prompt
│   ├── syntax.md    # RTEC grammar reference
│   └── examples/    # Few-shot examples per domain
├── core/            # ReAct loop implementation
│   ├── __init__.py
│   ├── agent.py     # Main agent class
│   └── schemas.py   # Pydantic models
├── apps/            # Application registry
│   └── voting/      # Example app
├── cli.py           # Interactive CLI
└── config.py        # Configuration
```

## Quick Start

```bash
# From the RTEC-mcp repo root:

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
uv pip install -e "agent/[dev]"

# List available apps
python -m agent.cli apps

# Show vocabulary for an app
python -m agent.cli vocab voting

# Show RTEC syntax reference
python -m agent.cli syntax

# Generate gold intervals for an app (run once)
python -m agent.cli gold voting

# Start interactive chat session
python -m agent.cli chat voting

# Run a single request
python -m agent.cli run voting "Generate rules for the status fluent"
```

## ReAct Loop

The agent follows a Think → Act → Observe cycle:

1. **Think**: Reason about current state, decide next action
2. **Act**: Call a tool (compile, run, compare, etc.)
3. **Observe**: Parse tool output, update understanding
4. **Repeat** until task complete or max iterations

## Tools

| Tool | Purpose |
|------|---------|
| `get_syntax_docs()` | RTEC grammar + constructs |
| `get_vocabulary(app)` | Events, fluents, entities for app |
| `compile_rules(app, rules)` | Syntax check via compiler |
| `run_rtec(app)` | Execute recognition |
| `compare_to_gold(app)` | Behavioral evaluation (F1) |
| `generate_gold(app)` | Create gold from expert rules |
