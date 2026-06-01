"""Repair loop — LangGraph StateGraph wiring the rule generation cycle.

Topology (see CLAUDE.md §4):

    generate ──▶ execute ──▶ compare ──▶ build_feedback ──┐
       ▲                                                   │
       └─────────── repair routing ◀──────────────────────┘

Acceptance is strict best-so-far: only commit a repair when F1 *strictly*
improves on the best seen so far (CLAUDE.md §4 acceptance policy + the formal
monotonicity lemma). Do not regress this.
"""
