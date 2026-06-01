"""LangSmith evaluation harness — datasets, evaluators, and run aggregation.

F1 is computed inside evaluator functions, never stored in dataset outputs
(it is per-run, not a fixed property of an example — CLAUDE.md §6).
"""
