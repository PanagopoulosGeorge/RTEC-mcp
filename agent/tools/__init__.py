"""RTEC tool implementations."""

from .compile import compile_rules
from .execute import run_rtec
from .evaluate import compare_to_gold, generate_gold
from .registry import get_vocabulary, get_syntax_docs, load_app, list_apps
from .inspect import read_rules

__all__ = [
    "compile_rules",
    "run_rtec",
    "compare_to_gold",
    "generate_gold",
    "get_vocabulary",
    "get_syntax_docs",
    "read_rules",
    "load_app",
    "list_apps",
]

# ── Shared schema objects ──────────────────────────────────────────────────────
# Defined once and referenced by both TOOL_DEFINITIONS and QA_TOOL_DEFINITIONS
# so each list is independent (no fragile index references).

_GET_SYNTAX_DOCS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_syntax_docs",
        "description": "Get RTEC syntax documentation including grammar rules, constructs (initiatedAt, terminatedAt, holdsFor, etc.), and examples.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
}

_GET_VOCABULARY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_vocabulary",
        "description": "Get the available vocabulary (events, fluents, entities, thresholds) for an application domain.",
        "parameters": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Application name (e.g., 'voting', 'maritime')"
                }
            },
            "required": ["app"]
        }
    }
}

# ── Builder tool definitions ───────────────────────────────────────────────────
# run_rtec is excluded: compare_to_gold calls it internally, so an explicit
# run_rtec call before compare_to_gold doubles RTEC execution for no gain.
TOOL_DEFINITIONS = [
    _GET_SYNTAX_DOCS_SCHEMA,
    _GET_VOCABULARY_SCHEMA,
    {
        "type": "function",
        "function": {
            "name": "compile_rules",
            "description": "Compile RTEC rules to check for syntax errors. Returns success status and any error messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    },
                    "rules": {
                        "type": "string",
                        "description": "Prolog rules to compile (initiatedAt, terminatedAt, holdsFor clauses, etc.)"
                    }
                },
                "required": ["app", "rules"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_to_gold",
            "description": (
                "Compile, run RTEC, and compare results against the gold standard. "
                "Returns F1 score and interval differences (false positives, false negatives). "
                "Pass `fluents` to scope the score (and convergence) to only the fluent(s) "
                "the user asked for; omit it to evaluate the whole event description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    },
                    "fluents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional fluent names to scope the comparison, e.g. [\"gap\"]. "
                            "Only these fluents count toward per_fluent, diffs, and the F1 "
                            "used for convergence. Omit to evaluate every fluent in the gold standard."
                        )
                    }
                },
                "required": ["app"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_gold",
            "description": "Generate gold standard intervals by running RTEC with expert rules. Only needed once per app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    }
                },
                "required": ["app"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_rules",
            "description": (
                "Read back the rules you most recently compiled (generated_rules.prolog). "
                "Useful before a new compile_rules() call to recall every fluent you defined, "
                "since compile_rules() REPLACES the whole rule set. "
                "Returns an error if you have not compiled anything yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    }
                },
                "required": ["app"]
            }
        }
    }
]


# ── QA agent tool definitions ──────────────────────────────────────────────────
# Read-only tools: never compile or mutate an app.
# get_vocabulary is included here so the QA agent can answer questions about
# the domain vocabulary (events, entities, thresholds) on demand.
QA_TOOL_DEFINITIONS = [
    _GET_SYNTAX_DOCS_SCHEMA,
    _GET_VOCABULARY_SCHEMA,
    {
        "type": "function",
        "function": {
            "name": "read_rules",
            "description": "Read the actual Prolog rules for an app. Use to explain how a fluent is defined or why it holds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["expert", "generated"],
                        "description": "Which rules to read: 'expert' (ground truth, default) or 'generated' (agent output)."
                    }
                },
                "required": ["app"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recognize",
            "description": "Run RTEC and return the time intervals during which each fluent holds. Use this to answer 'when does X hold?' questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app": {
                        "type": "string",
                        "description": "Application name"
                    },
                    "source": {
                        "type": "string",
                        "enum": ["expert", "generated"],
                        "description": "Which rules to run: 'expert' (ground truth, default) or 'generated' (agent output)."
                    }
                },
                "required": ["app"]
            }
        }
    }
]
