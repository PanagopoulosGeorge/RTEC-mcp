# AGENTS.md

> Operating guide for AI coding agents (Claude Code, Cursor) working in this repository.
> Read this fully before writing code. The single most important rule is in **§3**:
> a rule that compiles is **not** a rule that is correct.

---

## 1. What this project is

This repository is a **fork of RTEC** (Run-Time Event Calculus) with an **AI layer built on top of it**. RTEC is a Prolog/logic-programming engine for **composite (complex) event recognition**: given an *event description* (a set of rules defining composite activities) and an input stream of *simple events*, it computes the maximal intervals during which each composite activity holds.

The AI layer has **two capabilities**:

1. **Rule generation** — turn a natural-language description of a composite activity into a valid RTEC event description (Prolog rules), so a domain expert who does not know logic programming can author recognition rules.
2. **Querying** — turn a natural-language question about recognition results into a query executed against RTEC's output (e.g. "which vessels were loitering longer than an hour last Tuesday?"), returning a grounded answer.

This is MSc thesis work. The scientific contribution is **not** "an LLM can write Prolog." It is: **execution-guided iterative self-repair produces measurable improvement in rule quality, with RTEC execution against ground-truth intervals as the behavioral oracle.** Keep that framing in mind — it dictates many of the constraints below.

Domains: **MSA** (Maritime Situational Awareness, real AIS data — the primary domain) and **HAR** (Human Activity Recognition — a secondary/extension domain).

---

## 2. RTEC domain primer (read before generating any rule)

Prolog conventions: variables start with an **upper-case** letter; predicates and constants start **lower-case**; each rule ends with `.`; the head is separated from the body by `:-`. A *fluent* is a property whose value can change over time; `F=V` means fluent `F` has value `V`. Boolean fluents use values `true`/`false`.

### Core predicates

| Predicate | Meaning |
|---|---|
| `happensAt(E, T)` | Event `E` occurs at time `T` |
| `holdsAt(F=V, T)` | Fluent `F` has value `V` at time `T` |
| `holdsFor(F=V, I)` | `I` is the list of maximal intervals during which `F=V` holds continuously |
| `initiatedAt(F=V, T)` | At `T`, a period for which `F=V` is **initiated** |
| `terminatedAt(F=V, T)` | At `T`, a period for which `F=V` is **terminated** |
| `union_all(L, I)` | `I` = union of the interval-lists in list `L` |
| `intersect_all(L, I)` | `I` = intersection of the interval-lists in list `L` |
| `relative_complement_all(I', L, I)` | `I` = `I'` minus every interval-list in `L` |
| `intDurGreater(I, Threshold, I2)` | `I2` = the intervals in `I` whose duration exceeds `Threshold` |

Built-in events: `start(F=V)` fires at each start point of a maximal interval of `F=V`; `end(F=V)` fires at each end point. Negation is **negation-by-failure**, written `not`. A **free** variable (value we don't constrain) is written with a leading underscore, e.g. `_Status`.

### The two fluent regimes — getting this wrong is a silent semantic error

There are exactly two ways to define a composite activity. Choosing the wrong one produces rules that compile but behave incorrectly.

**(a) Simple fluents** — defined by `initiatedAt`/`terminatedAt` rules. Use when the activity is **event-driven**: it starts and stops on discrete events. The first body literal of an `initiatedAt` rule is a positive `happensAt`, optionally followed by positive/negative `happensAt`/`holdsAt` literals and background knowledge. We call these **EDFs (event-driven fluents)** in thesis terminology.

```prolog
initiatedAt(withinArea(Vessel, AreaType)=true, T) :-
    happensAt(entersArea(Vessel, Area), T),
    areaType(Area, AreaType).

terminatedAt(withinArea(Vessel, AreaType)=true, T) :-
    happensAt(leavesArea(Vessel, Area), T),
    areaType(Area, AreaType).
```

**(b) Statically determined fluents (SDFs)** — defined by a single `holdsFor(F=V, I)` rule whose body combines the `holdsFor` intervals of *other* fluents using `union_all` / `intersect_all` / `relative_complement_all` (and optionally `intDurGreater` for minimum-duration constraints). Use when the activity is defined as **a relationship between the durations of other activities** rather than by discrete start/stop events.

```prolog
holdsFor(rendezVous(V1, V2)=true, I) :-
    holdsFor(proximity(V1, V2)=true, Ip),
    not oneIsTug(V1, V2),
    not oneIsPilot(V1, V2),
    holdsFor(lowSpeed(V1)=true, Il1),
    holdsFor(stopped(V1)=farFromPorts, Is1),
    union_all([Il1, Is1], I1b),
    holdsFor(lowSpeed(V2)=true, Il2),
    holdsFor(stopped(V2)=farFromPorts, Is2),
    union_all([Il2, Is2], I2b),
    intersect_all([I1b, I2b, Ip], If), If \= [],
    holdsFor(withinArea(V1, nearPorts)=true, Iw1),
    holdsFor(withinArea(V2, nearPorts)=true, Iw2),
    holdsFor(withinArea(V1, nearCoast)=true, Iw3),
    holdsFor(withinArea(V2, nearCoast)=true, Iw4),
    relative_complement_all(If, [Iw1, Iw2, Iw3, Iw4], Ii),
    thresholds(rendezvousTime, RendezvousTime),
    intDurGreater(Ii, RendezvousTime, I).
```

**Heuristic for the agent:** if the NL says "starts when … ends when …" with named events → simple fluent. If it says "lasts as long as …" / "while …" / "for the duration that …" combining other activities → SDF. Phrases like "cannot be arbitrarily brief" / "exceeds a minimum duration" → SDF with `intDurGreater`.

### Domain vocabulary

The complete catalogue of MSA/HAR events, input fluents, and background-knowledge predicates lives in **`prompts/` domain YAML files** (`prompts/msa.yaml`, `prompts/har.yaml`), loaded via the domain loader. **Never invent vocabulary.** Every event, fluent, value, and threshold key the LLM emits must exist in the loaded domain spec. Inventing a plausible-but-absent predicate is one of the most common failure modes.

---

## 3. THE CENTRAL PROBLEM — compilable ≠ correct (read twice)

The hardest and most important failure mode in this project: **the LLM produces Prolog that compiles cleanly and uses only valid vocabulary, but recognizes the wrong intervals.** A clean compile, valid grounding, and correct category placement tell you nothing about behavioral correctness. The bug lives in the *combination* of predicates, not the form of any one of them.

**Therefore: a compile pass is NOT a correctness signal. The only valid oracle for an RTEC rule is RTEC execution scored against ground-truth intervals.** Any agent change that claims a fluent is "fixed" or "working" must back that claim with an execution result (point-set F1), never with "it compiles" or "it looks right."

Canonical motivating example (`highSpeedNearCoast`): a structurally flawless rule — 0 violations, correct grounding, valid vocabulary — that was behaviorally wrong because of two semantic divergences invisible to any static check:
- **Boundary comparator**: rule used `Speed < SpeedLimit` where ground truth used an *inclusive* `inRange(Speed, 0, SpeedLimit)`; they disagree at `Speed == SpeedLimit`, drifting every interval endpoint by a tick.
- **Wrong termination event**: rule terminated on "vessel leaves *any* area"; ground truth terminated only on "vessel leaves `nearCoast` specifically." The rule over-terminated.

### The four silent RTEC failure modes (high-impact, under-documented)

When a rule compiles but F1 is low, suspect these first, in roughly this order:

1. **Missing grounding declaration.** Every authored fluent needs its grounding declared, or it silently produces nothing. Grounding generation should be **deterministic**, derived from the rule head — not left to the LLM.
2. **Wrong fluent regime** — an activity modelled as a simple fluent that should be an SDF, or vice versa (see §2).
3. **Unbound variables under negation** — a variable appearing only inside a `not(...)` is unsafe and changes semantics or fails silently.
4. **Right-open interval off-by-one** — effects in RTEC take hold at `T+1`, not `T`. An endpoint that is one tick off across every interval is almost always this.

When diagnosing, sample the *disagreement timestamps* between produced and ground-truth intervals and map them to a cause class (boundary comparator / wrong termination / missing branch / off-by-one). Feed that as the next repair instruction. Do not feed back vague "try again" signals.

### The EDF/SDF asymmetry (the central thesis finding)

EDFs converge fast, often single-shot. **SDFs requiring chained interval algebra (`union_all`/`intersect_all`/`relative_complement_all`/`intDurGreater`) plateau below convergence.** The repair loop can tell the LLM *which predicate is missing* but struggles to teach it how to *restructure interval algebra*. This asymmetry is the primary research contribution — do not "fix" it by hiding it. When working on SDFs, surface the gap clearly; it is data, not a bug to paper over.

---

## 4. Architecture

The AI layer is a **LangGraph `StateGraph`** — a deterministic state machine, **not** a linear LangChain pipeline and **not** a free-form ReAct agent. This is a deliberate, defended choice: reproducibility for thesis evaluation requires deterministic, conditional routing over shared typed state with checkpointing. Do **not** propose replacing it with LangChain chains, an autonomous ReAct loop, or a single stronger agent — those have all been explicitly rejected.

**Rule-generation loop** (the mature, central system):

```
generate ──▶ execute ──▶ compare ──▶ build_feedback ──┐
   ▲                                                    │
   └──────────────── (repair routing) ◀─────────────────┘
```

- **Generator agent** — *stateless*. Writes Prolog rules from the spec + current feedback. Holds no memory across iterations.
- **Orchestrator agent** — *stateful*. Diagnoses failures from the execution diff, issues a typed repair instruction, and performs deterministic routing. Its prompt should **prioritise reasoning over output-form specification** — over-specified output contracts degrade diagnosis quality.
- **`RepairState`** — typed (TypedDict) shared state threaded through every node.
- **Oracle** — `compare` runs RTEC via the executor and computes **point-set F1** against ground-truth intervals, sliced by `fluent_type` (EDF vs SDF).

**Acceptance policy (must hold):** F1 across iterations is **not** guaranteed monotonic. The loop must implement a strict **best-so-far** policy: only commit a repaired rule when its F1 *strictly improves* on the best seen so far; otherwise keep the previous best. This is also the basis of the formal monotonicity lemma in the thesis — do not regress it.

**Querying layer** (newer, lighter): translate an NL question into a query over recognition output. Resolve named activities/entities/time-windows against the domain spec, execute the relevant `holdsAt`/`holdsFor` query (or filter recognized intervals) through the same RTEC executor, and return a grounded answer. **The query path uses the same executor and the same "execution is the oracle" discipline** — never fabricate an answer the engine did not produce. If a query references vocabulary not in the domain spec, say so rather than guessing.

---

## 5. Hard invariants (do not violate)

1. **Never modify the RTEC engine itself.** It is the oracle and (where applicable) a pinned submodule. Treat it as read-only. Do not bump or edit it.
2. **RTEC execution is the only correctness signal.** No "it compiles," "looks correct," or LLM self-confidence as a substitute. LLM self-reported confidence is known-unreliable here and must not be used as a validation metric.
3. **Never invent domain vocabulary.** Events, fluents, values, thresholds must come from the loaded domain spec.
4. **Grounding is deterministic**, derived from the rule head — not LLM-authored.
5. **Keep the StateGraph.** No silent swaps to LangChain pipelines, ReAct, or a single mega-agent.
6. **Preserve the best-so-far acceptance policy.** Do not let a worse-F1 repair overwrite a better one.
7. **Keep EDF/SDF tagging intact** on every evaluation output — the performance split is the thesis's central measurement.
8. **`view` apparent `.docx` reference files directly** (e.g. `llms.docx` is plain UTF-8 despite the extension). Do not run document-extraction utilities on them.

---

## 6. Engineering conventions

- **Python 3.12.** Strict **mypy** and **ruff** must pass throughout. Build backend: **hatchling**. **pre-commit** hooks must stay green.
- **SWI-Prolog (`swipl`)** is the RTEC runtime. Before trusting any pipeline result, confirm `swipl --version` works and a bare RTEC example runs standalone — if the engine doesn't run, nothing above it matters.
- **Evaluation = LangSmith.** Datasets carry ground-truth expected intervals per entity + a `fluent_type` tag. **F1 is computed inside evaluator functions, never stored in dataset outputs** (it's per-run, not a fixed property of an example). Descriptive fields belong in `inputs`/`metadata`.
- **LLM providers** go through a multi-provider factory (OpenAI/GPT-4o primary; also Claude, Gemini, Ollama, GLM). New providers are added at the factory, not inline.
- **Fixtures** are annotation-driven JSON: NL spec + event stream + ground-truth intervals + domain facts + prerequisites. The behavioral annotation — not a reference Prolog rule — is the input contract. (Requiring expert-written reference rules was Approach 1 and was abandoned; do not reintroduce that dependency.)
- **No single-shot baseline exists yet.** It is required to prove the repair loop adds value. If asked to run experiments, establishing this baseline is high priority.

### Typical commands

```bash
make install        # set up env (Python 3.12)
make lint           # ruff + mypy strict
make test           # unit tests
make smoke-test     # run a toy fluent end-to-end through real RTEC/swipl
```

(Adjust to the actual Makefile targets present in this fork; keep `lint`, `test`, and a real-engine `smoke-test` available.)

---

## 7. Working style for agents in this repo

- **Finding-first, then fix.** Diagnose the root cause and state it before changing code. A clear diagnosis ("F1 dropped because the SDF used `intersect_all` where it needed `union_all` on the speed branch") is worth more than a speculative patch.
- **Back every correctness claim with an execution result**, never a compile or a vibe.
- **Prefer minimal, reviewable diffs.** This is a thesis codebase under evaluation; reproducibility beats cleverness.
- **When a change touches the repair loop, the oracle, or the state schema, flag it explicitly** — these are the parts the thesis is measuring.
- **Surface SDF failures honestly.** They are the finding, not an embarrassment to hide.

---

*This file is the standard for both Claude Code (reads `CLAUDE.md`; symlink `ln -s AGENTS.md CLAUDE.md` if needed) and Cursor (reads `AGENTS.md` natively in v1.6+). Keep it concise — an overlong spec degrades agent performance.*

## Running RTEC

Two equivalent entry points; pick whichever fits the task.

**Bash driver** (canonical, no install needed beyond SWI-Prolog):

```bash
cd "execution scripts"
./run_rtec.sh --app=toy                          # default params from defaults.toml
./run_rtec.sh --app=maritime --window-size=20    # override any TOML field; "_" → "-"
```

Supported app names = table names in [execution scripts/defaults.toml](execution%20scripts/defaults.toml): `toy`, `maritime`, `maritime_allen`, `voting`, `netbill`, `caviar`, `ctm`. Adding a new app = add a TOML table + `examples/<app>/` directory; nothing else is auto-discovered.

**Python CLI** (`RTEC2`, defined in [execution scripts/RTEC2_cli.py](execution%20scripts/RTEC2_cli.py)):

```bash
uv venv .venv && source .venv/bin/activate
bash install.sh
RTEC2 --use-case maritime --path examples/maritime
```

The CLI's `--use-case` whitelist (`caviar`, `ctm`, `maritime`, `netbill`, `voting`, `toy`) is a *subset* of the bash driver's apps and does not include `maritime_allen`.

Results land in `examples/<app>/results/` (a log file + intervals file per window). These are gitignored.

## Scoring runs

The scoring package at [execution scripts/scoring/](execution%20scripts/scoring/) already computes the **point-set timepoint F1** the thesis uses as its oracle (§3). **Reuse it; do not reimplement F1.** Full seam mapped in [docs/ENGINE_NOTES.md](docs/ENGINE_NOTES.md) §4.

**CLI usage** (run from inside `execution scripts/scoring/` — relative imports `from utilities.X import ...` require that cwd):

```bash
cd "execution scripts/scoring"
python evaluate.py --gt path/to/gt.txt --test path/to/predicted.txt --out report.csv
```

Both `--gt` and `--test` files must be raw RTEC `recognitions(predictions, name, [[args], value], [(s,e),...]).` output, exactly as written to `examples/<app>/results/log-...-recognised-intervals.txt`. Across multiple windows the parser temporal-unions repeated `(name, args, value)` lines — pass the raw file as-is. Output CSV columns: `fluent, value, tp, fp, fn, precision, recall, f1`, one row per fluent-value pair (FVP), plus `avgscores,micro` and `avgscores,macro` rows.

**Importing from the AI layer** (preferred — supports EDF/SDF slicing):

```python
# cwd = execution scripts/scoring/, or that dir on sys.path
from utilities.parser  import parse_file         # path -> {(name,value): {args_tuple: [[s,e],...]}}
from utilities.compare import compare_ce, get_micro, get_macro

gt   = parse_file("path/to/gt.txt")
pred = parse_file("path/to/predicted.txt")
per_fvp = {fvp: compare_ce(gt[fvp], pred.get(fvp)) for fvp in gt}
for fvp in pred:                                  # catch FVPs only in pred (all FP)
    if fvp not in gt:
        per_fvp[fvp] = compare_ce({}, pred[fvp])
micro = get_micro(per_fvp)                        # {"tp","fp","fn","precision","recall","f1"}
macro = get_macro(per_fvp)
```

`compare_ce(gt_dict, test_dict)` returns the same `{tp, fp, fn, precision, recall, f1}` shape per FVP. **TP/FP/FN are timepoints**, computed as `sum(end - start)` over `temporal_intersection` / `temporal_difference` results — half-open `[s, e)` semantics, matching RTEC's right-open interval convention.

**EDF vs SDF slicing**: keys are `(fluent_name, value)` FVPs. The engine has no notion of EDF vs SDF; tag each FVP from the AI layer's own metadata, then run `get_micro` / `get_macro` over each subset to produce the EDF-vs-SDF split the thesis measures (§5 hard invariant 7).

**Gotchas**:
- **Do not `import evaluate`** — its CSV-writing block sits at module top level (lines 64+), so import triggers `NameError: output_file_path`. Either shell out to `python evaluate.py ...`, or import the `utilities/*` modules directly and skip `evaluate.py`.
- `evaluate.py` iterates only `for ce in ces_gt:` and silently drops FVPs that exist *only* in `--test` — spurious predictions for an FVP the GT never mentions don't appear in the report. The Python snippet above patches this with the explicit `for fvp in pred: if fvp not in gt:` pass; do the same in any new evaluator.
- Ground-truth files must use the same half-open interval convention as RTEC output. A one-tick endpoint drift across N intervals costs N timepoints (§3 failure mode 4).
- `parser.py` is hand-rolled string-splitting, not a Prolog parser. Args containing literal commas will tokenise wrong; keep entity IDs/atoms comma-free.

## install.sh gotcha

[install.sh](install.sh) temporarily renames things on disk to satisfy the legacy [setup.py](setup.py) layout, which expects a `RTECv2/` package: it creates `RTECv2/`, moves `src/` and `execution scripts/` into it (the latter becomes `RTECv2/scripts`), hides any `pyproject.toml` / `uv.lock` as `*.install.bak`, runs `uv pip install .` (or `pip3`), then restores everything via an `EXIT` trap.

If install is interrupted you can end up with `RTECv2/` lingering and `src/` / `execution scripts/` missing from the repo root. The `cleanup()` function at the top of `install.sh` is the recipe for unwinding by hand. Do not rename the canonical paths permanently — the rest of the repo (and the bash driver) assumes `src/` and `execution scripts/` at the root.
