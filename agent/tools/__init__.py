"""RTEC tool implementations."""

from .compile import compile_rules
from .execute import run_rtec
from .evaluate import compare_to_gold, generate_gold
from .registry import get_vocabulary, get_syntax_docs, load_app, list_apps

__all__ = [
    "compile_rules",
    "run_rtec", 
    "compare_to_gold",
    "generate_gold",
    "get_vocabulary",
    "get_syntax_docs",
    "load_app",
    "list_apps",
]

# Tool definitions for LLM function calling
TOOL_DEFINITIONS = [
    {
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
    },
    {
        "type": "function", 
        "function": {
            "name": "get_vocabulary",
            "description": "Get the available vocabulary (events, fluents, entities) for an application domain.",
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
    },
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
            "name": "run_rtec",
            "description": "Run RTEC event recognition using the currently compiled rules. Returns recognition intervals.",
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
            "name": "compare_to_gold",
            "description": "Compare current recognition results to gold standard intervals. Returns F1 score and interval differences (false positives, false negatives). Pass `fluents` to scope the score (and convergence) to only the fluent(s) the user asked for; omit it to evaluate the whole event description.",
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
                        "description": "Optional fluent names to scope the comparison to, e.g. [\"rich\"]. Only these fluents count toward per_fluent, diffs, and the F1 used for convergence. Omit to evaluate every fluent in the gold standard."
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
    }
]
