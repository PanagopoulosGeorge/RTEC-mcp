"""Stateful orchestrator agent.

Diagnoses failures from the execution diff, issues a typed repair instruction,
and performs deterministic routing for the StateGraph. Its prompt prioritises
reasoning over output-form specification — over-specified output contracts
degrade diagnosis quality (CLAUDE.md §4).

Implementation deferred to P2.
"""
