# Incremental Per-Fluent Evaluation Session

## Objective

Run the RTEC agent on the maritime domain one fluent at a time, in dependency
order. For each fluent:
1. Invoke `python -m agent.cli run maritime <fluent>`
2. Record the F1 score, number of iterations, and any failure mode.
3. If F1 < 0.95, diagnose why — inspect the system prompt context injected
   for that fluent and identify which piece of information was missing or
   misleading.
4. Propose a concrete, targeted fix to `vocabulary.yaml`, `system.md`, or
   `syntax.md` and apply it.
5. Re-run the fluent to confirm the fix improves F1.
6. Only move to the next fluent after the current one is ≥ 0.95 or you have
   exhausted 3 fix attempts.

## Execution Order

Respect the dependency hierarchy — a fluent that appears in another's body
must be run first so it exists in `generated_rules.prolog` when the dependent
fluent is attempted.

```
Layer 0 (input events only):
  withinArea, gap, stopped, lowSpeed, changingSpeed,
  highSpeedNearCoast, movingSpeed, drifting,
  tuggingSpeed, trawlSpeed, trawlingMovement,
  sarSpeed, sarMovement

Layer 1 (depends on layer 0):
  underWay        → depends on movingSpeed
  anchoredOrMoored→ depends on stopped, withinArea
  tugging         → depends on tuggingSpeed, withinArea
  trawling        → depends on trawlSpeed, trawlingMovement, withinArea
  inSAR           → depends on sarSpeed, sarMovement
  loitering       → depends on withinArea, gap, underWay

Layer 2 (depends on layer 1):
  rendezVous      → depends on withinArea, gap, tugging
  pilotOps        → depends on withinArea, gap
```

Process layers left-to-right within each layer (order within a layer does
not matter since they are independent).

## Per-Fluent Trace Template

After each fluent run, fill in this template and keep a running table:

```
Fluent       : <name>
F1           : <score>
Iterations   : <n>
Verdict      : PASS | FAIL
Failure mode : (if FAIL) spurious / missing / compile_error / zero_recognition
Root cause   : (if FAIL) missing context / wrong example / prompt ambiguity /
               RTEC constraint violation
Fix applied  : <description of change made to vocabulary.yaml / system.md /
               syntax.md, or "none">
F1 after fix : <score>
```

## Analysis Focus Areas

When diagnosing a failure, check these in order:

1. **Zero recognitions** — the fluent never fires at all.
   - Check: is the preamble (`dynamicDomain`, `collectIntervals`) included?
   - Check: are the grounding facts correct (vessel/1, areaType/1 etc.)?
   - Check: does the LLM include ALL dependency rules in the same compile call?

2. **Spurious intervals** (precision low, recall OK) — rule too permissive.
   - Check: is the termination condition too weak or missing?
   - Check: does the LLM use `leavesArea` (area ID, not type) when it should
     use `happensAt(end(withinArea(V, T)=true), T)`?
   - Check: is a `thresholds/2` binding missing in a terminatedAt clause?

3. **Missing intervals** (recall low, precision OK) — rule too restrictive.
   - Check: is an initiatedAt condition referencing the wrong threshold key?
   - Check: is a dependency fluent undefined at runtime (absent from compile)?

4. **Compile error** — always means a Prolog syntax or grounding problem.
   - Check: disjunction `(A ; B)` in rule body (RTEC does not support it).
   - Check: arithmetic on unbound variable (thresholds not bound before use).
   - Check: `not` instead of `\+`.

5. **Context injection quality** — if the LLM's reasoning shows it did not
   know a fact that IS available, identify which block failed to convey it:
   - `vocabulary.yaml` → `preamble`, `background_predicates`, `examples`
   - `system.md` → workflow rules, debugging tips
   - `syntax.md` → RTEC construct documentation

## Improvement Actions (pick the most targeted fix)

| Problem observed | Fix location | What to change |
|---|---|---|
| LLM uses `leavesArea(V, nearCoast)` | `system.md` fp_dominated nudge or example | Add note: leavesArea takes area ID, not type; use `end(withinArea(...)=true)` |
| LLM forgets to bind thresholds before arithmetic | `system.md` rule #6 or example | Reinforce per-clause binding requirement with a concrete counter-example |
| LLM drops dependency rules on 2nd compile | `system.md` CRITICAL section | Strengthen the "include ALL rules" instruction with a numbered list |
| LLM does not know `start(F=V)` / `end(F=V)` exist | `syntax.md` | Add a dedicated section on built-in interval-boundary events |
| LLM uses wrong portStatus / areaType atom | `vocabulary.yaml` entities block | Verify all entity value lists are exhaustive and correctly named |
| LLM generates a holdsFor rule for a simple fluent | `system.md` or `vocabulary.yaml` examples | Add a worked counter-example showing the wrong and right classification |

## Running Commands

```bash
cd /path/to/RTEC-agent
source .venv/bin/activate && set -a && source .env && set +a

# Run one fluent
python3 -m agent.cli run maritime <fluent> 2>&1 | tee /tmp/<fluent>.log

# After a fix, re-run to verify
python3 -m agent.cli run maritime <fluent> 2>&1 | tail -20
```

Note: `generated_rules.prolog` accumulates across runs in the same session.
The agent seeds itself from it at the start of each `run` call. If you need
to restart from scratch, delete it:
```bash
rm agent/apps/maritime/generated_rules.prolog
```

## Session Output

Maintain a results table here and update it after each fluent:

| Fluent | F1 | Iters | Pass? | Root cause (if fail) | Fix |
|---|---|---|---|---|---|
| withinArea | 1.0 | — | PASS | (pre-learned, skipped) | — |
| gap | 1.0 | 3 | PASS | iter 1–2: dropped withinArea from compile → holdsAt always false → all gaps classified farFromPorts | agent self-fixed iter 3 by re-including withinArea |
| stopped | — | — | — | — | — |
| lowSpeed | — | — | — | — | — |
| changingSpeed | — | — | — | — | — |
| highSpeedNearCoast | 1.0 | 3 | PASS | iter 1: change_in_speed events + holdsAt(velocity) → F1=0; iter 2: leavesArea for area exit → spurious F1=0.35; iter 3: end(withinArea) → F1=1.0 (agent missed final compare) | agent self-fixed; prompt fix held deps on iter 1 |
| movingSpeed | — | — | — | — | — |
| drifting | — | — | — | — | — |
| tuggingSpeed | — | — | — | — | — |
| trawlSpeed | — | — | — | — | — |
| trawlingMovement | — | — | — | — | — |
| sarSpeed | — | — | — | — | — |
| sarMovement | — | — | — | — | — |
| underWay | — | — | — | — | — |
| anchoredOrMoored | — | — | — | — | — |
| tugging | — | — | — | — | — |
| trawling | — | — | — | — | — |
| inSAR | — | — | — | — | — |
| loitering | — | — | — | — | — |
| rendezVous | — | — | — | — | — |
| pilotOps | — | — | — | — | — |
