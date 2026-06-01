"""Typed repair-feedback construction.

Maps disagreement timestamps from a ``ScoreResult`` to a cause class
(boundary comparator / wrong termination / missing branch / off-by-one — the
four silent failure modes in CLAUDE.md §3) and packages it as a structured
instruction for the next generator call. Vague "try again" feedback is
forbidden.

Implementation deferred to P2.
"""
