"""LangSmith evaluator functions.

Each example carries ground-truth expected intervals per entity and a
``fluent_type`` tag (EDF / SDF). The evaluator runs the rule through the
``EnginePort``, scores the output via ``ScoringPort``, and reports F1 sliced
by fluent type — the central thesis measurement.

Implementation deferred to P2.
"""
