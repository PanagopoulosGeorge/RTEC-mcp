"""LangGraph ``StateGraph`` assembly for the repair loop.

Deliberately a deterministic state machine, NOT a free-form ReAct agent and
NOT a linear LangChain pipeline (CLAUDE.md §4 — these alternatives have all
been explicitly rejected; do not propose replacing the graph).

Implementation deferred to P2.
"""
