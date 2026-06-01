"""``RepairState`` — typed shared state threaded through every LangGraph node.

Defined as a ``TypedDict`` (LangGraph's preferred shape). Carries the current
rule, the latest ``ExecutionResult`` and ``ScoreResult``, the best-so-far
snapshot, the typed repair feedback, and the iteration counter.

Implementation deferred to P2.
"""
