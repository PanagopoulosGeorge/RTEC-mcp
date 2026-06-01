"""rtec_llm — AI layer over the RTEC composite-event-recognition engine.

Two capabilities:

* **Rule generation**: turn an NL description of a composite activity into a
  valid RTEC event description (Prolog rules).
* **Querying**: answer NL questions about recognition results.

Architecture is a LangGraph StateGraph (deterministic state machine) with two
agents (stateless generator, stateful orchestrator), a typed RepairState, and
RTEC execution + point-set F1 as the behavioural oracle.

See:
- `docs/ARCHITECTURE.md` for the dependency diagram and target tree.
- `docs/ENGINE_NOTES.md` for the engine and scoring seams.
- `CLAUDE.md` for invariants and conventions.
"""
