"""Querying layer — NL question → query over RTEC recognition output.

Uses the same ``EnginePort`` as the rule-generation loop and the same
"execution is the oracle" discipline: never fabricate an answer the engine
did not produce (CLAUDE.md §4).
"""
