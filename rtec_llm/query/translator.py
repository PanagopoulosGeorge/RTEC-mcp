"""NL → recognition-output query translator.

Resolves named activities / entities / time-windows against the loaded
``Vocabulary``, dispatches the relevant ``holdsAt`` / ``holdsFor`` query (or
filters recognised intervals) through the ``EnginePort``, and returns a
grounded answer. If a query references vocabulary not in the spec, the
translator says so rather than guessing (CLAUDE.md §4).

Implementation deferred to P2.
"""
