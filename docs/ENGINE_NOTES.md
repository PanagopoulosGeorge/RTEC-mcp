# ENGINE_NOTES — RTEC bare-engine sanity check (P0)

Read-only inventory of the RTEC runtime, executor, scoring layer, and domain
layouts that the AI layer will wrap. **No code under `src/` or
`execution scripts/` was modified.** Run-produced artifacts under
`examples/*/results/` are gitignored.

Date of run: 2026-05-30 (macOS / arm64-darwin).

---

## 1. Toolchain — verified

```
$ swipl --version
SWI-Prolog version 9.2.9 for arm64-darwin
```

Above the minimum needed by RTEC (CLAUDE.md only mandates "swipl works
standalone"). No YAP installed; the bash driver and CLI both default to `swipl`.

---

## 2. Bare-engine smoke runs — both green

### 2.1 Toy (5 input events, instantaneous)

```
cd "execution scripts"
./run_rtec.sh --app=toy
```

The driver recompiles `examples/toy/resources/patterns/rules.prolog` →
`compiled_rules.prolog`, then runs one window `(0, 50]`. Result:

- Input entities: 5
- Output FVPs: 5 (`rich`, `location=pub`, `location=home`, `location=work`, `happy`)
- Output intervals: 5, total 54 timepoints
- Wall time: <1 ms

Recognised-intervals file written to
[examples/toy/results/log-swi-50-50-csv-file-recognised-intervals.txt](../examples/toy/results/log-swi-50-50-csv-file-recognised-intervals.txt).
Sample line:

```
recognitions(predictions,location,[[chris],pub],[(18,22)]).
```

### 2.2 Maritime (real AIS, Brest dataset)

The full dataset spans ~60 days (~16M lines). To get a fast end-to-end
sanity run, I ran one default-sized window (36 000 s ≈ 10 h):

```
cd "execution scripts"
./run_rtec.sh --app=maritime --start-time=1443650400 --end-time=1443686400
```

- Input entities: 37 218
- Output FVPs: 1 247
- Output intervals: 7 422 (≈ 8.16M timepoints)
- Wall time: 968 ms

Output: [examples/maritime/results/log-swi-36000-36000-csv-file-recognised-intervals.txt](../examples/maritime/results/log-swi-36000-36000-csv-file-recognised-intervals.txt).
Sample line:

```
recognitions(predictions,withinArea,[[209273000,fishing],true],
  [(1443681546,1443681669),(1443681895,1443681955),
   (1443684266,1443685589),(1443685596,1443686401)]).
```

A previous full-day run already exists in the same directory
(`log-swi-86400-86400-...`, ~191 MB, 1.34M lines) — both files use the
identical RTEC output format, so the existing parser handles them as-is.

---

## 3. Executor seam

There are **two equivalent entry points** wrapping the same Prolog engine. The
AI layer must commit to one — they behave differently in non-trivial ways.

### 3.1 Bash driver — `execution scripts/run_rtec.sh`

The canonical entry. Flow (see [execution scripts/auxiliary/utils.sh](../execution%20scripts/auxiliary/utils.sh)):

1. Parses `--app=<name>` and arbitrary `--<param>=<value>` overrides (underscore
   in TOML key → dash on CLI).
2. Loads defaults from [execution scripts/defaults.toml](../execution%20scripts/defaults.toml)
   for any param not set on the command line.
3. **Compiles** `event_description` (raw `rules.prolog`) via
   `auxiliary/compile.sh`, producing `compiled_rules.prolog` next to it.
4. Invokes `swipl -l continuousQueries.prolog -g 'continuousQueries(<app>, [<params>])'`.
5. Writes results to `<results_directory>` (default
   `../examples/<app>/results/`).

Supported app names = `[toy, maritime, maritime_allen, voting, netbill, caviar, ctm]`.

### 3.2 Python CLI — `execution scripts/RTEC2_cli.py` (`RTEC2` entry-point)

Installed by [install.sh](../install.sh); see CLAUDE.md for the install gotcha
(the legacy `setup.py` layout it temporarily creates).

```
RTEC2 --use-case maritime --path examples/maritime
```

Internally builds a parameter list and runs the same
`swipl -l continuousQueries.prolog -g 'continuousQueries(<app>, [<params>])'`.

**Differences from the bash driver — read carefully, these are real footguns:**

| Aspect | Bash driver | Python CLI |
|---|---|---|
| App whitelist | adds `maritime_allen` | omits it (`use_case_enum` line 14) |
| Pre-compile? | **Yes** (calls `compile.sh` first) | **No** — directly consults `compiled_rules.prolog` (line 118) |
| Where it looks for KB Prolog files | `event_description` + `background_knowledge` listed in `defaults.toml` | `<path>/resources/patterns/compiled_rules.prolog` + every `.prolog` in `<path>/resources/auxiliary/` and `<path>/dataset/auxiliary/` |
| Param source | `defaults.toml` | argparse defaults; falls back to Prolog's internal defaults if not passed |

**Implication for the AI layer:** if generated rules are run through the CLI,
the AI layer must compile them first (call `compile.sh` or run the bash driver
instead), otherwise `compiled_rules.prolog` will be stale and the LLM's edits
will be silently ignored. **Recommendation: wrap the bash driver** — it
already handles the compile step and matches the reproducible TOML config that
underpins the existing examples.

> **Update (P2):** the implemented adapter
> ([rtec_subprocess.py](../rtec_llm/adapters/engine/rtec_subprocess.py)) runs the
> bash driver's two internal steps directly — `compile.sh` then
> `swipl … continuousQueries.prolog` — rather than `run_rtec.sh` itself, for
> clean compile-vs-runtime error separation and to avoid the driver's `sleep 10`
> on a failed compile. Recognition output is smoke-verified identical; params
> come from the `Window` instead of `defaults.toml`. See
> [ARCHITECTURE.md](ARCHITECTURE.md) §8 / decision P2-1.

### 3.3 Output format (both entry points)

Per window, one line per `(fluent name, args, value)` triple:

```
recognitions(predictions, <fluent_name>, [[<arg1>,<arg2>,...], <value>],
             [(<t_start>,<t_end>), ...]).
```

- Times are integers (UNIX epoch in maritime; tick counts elsewhere).
- Boolean fluents emit `true`/`false`; multi-valued fluents emit the value name.
- File naming pattern:
  `log-<prolog>-<window>-<step>-<input_mode>-<output_mode>-recognised-intervals.txt`
  and a sibling `...-log.txt` with timing/size stats.
- One additional `results.log` (legacy, low-value).
- Across multiple windows, the *same* `(name, args, value)` triple may emit
  multiple lines — `parser.py` amalgamates via temporal union (see §4).

---

## 4. Scoring seam — REUSE this, do not reimplement

All under [execution scripts/scoring/](../execution%20scripts/scoring/). Python
3, no dependencies beyond stdlib. The four files:

### 4.1 [`utilities/parser.py`](../execution%20scripts/scoring/utilities/parser.py)

- `parse_line(line) -> dict` — splits one `recognitions(...)` line into
  `{name, args, value, intervals}`. Hand-rolled string splitting (no Prolog
  parser); will break if rule heads contain commas in args.
- `parse_file(path) -> dict[(name, value)] -> dict[args_tuple] -> list[[s,e]]`
  — keys files by **fluent-value pair** `(name, value)` (e.g.
  `("withinArea", "true")`), with one nested dict of `args` → interval list.
  When the same `(name, value, args)` appears in multiple windows it temporal-
  unions the interval lists.
- `maximal_intervals_from_str` / `_to_str` — round-trip serialisation helpers.

### 4.2 [`utilities/temporal_ops.py`](../execution%20scripts/scoring/utilities/temporal_ops.py)

Three pure functions over sorted interval lists `[[start, end], ...]`:

- `temporal_union(i1, i2) -> merged`
- `temporal_intersection(i1, i2) -> overlap`
- `temporal_difference(i1, i2) -> i1 minus i2`

Used by both the parser (amalgamation) and `compare`.

### 4.3 [`utilities/compare.py`](../execution%20scripts/scoring/utilities/compare.py)

- `precision(tp, fp)`, `recall(tp, fn)`, `f1_score(tp, fp, fn)` — guards
  divide-by-zero with `0.0`.
- `get_timepoints_from_intervals(intervals) -> int` — sums `(end - start)`.
  **This is the metric unit: timepoints, not intervals.** Half-open `[s, e)`
  semantics matches RTEC's right-open interval convention.
- `compare_ce(gt_dict, test_dict | None) -> {tp, fp, fn, precision, recall, f1}`
  — for one FVP across all entity-arg instances:
  - TP = sum of timepoints in `temporal_intersection(gt, test)` per args
  - FP = timepoints in `temporal_difference(test, gt)`
  - FN = timepoints in `temporal_difference(gt, test)`
  - args present only in `test` → all FP; args present only in `gt` → all FN
- `get_micro(results)` — aggregate TP/FP/FN over all FVPs, then derive P/R/F1.
- `get_macro(results)` — arithmetic mean of per-FVP P/R/F1 (TP/FP/FN set to -1 sentinel).

### 4.4 [`evaluate.py`](../execution%20scripts/scoring/evaluate.py) (top-level)

CLI: `python evaluate.py --gt <file> --test <file> --out <csv>`. Writes a CSV
of `fluent, value, tp, fp, fn, precision, recall, f1` rows, one per FVP, plus
two `avgscores` rows for micro and macro. Example
[scoring/example/report.csv](../execution%20scripts/scoring/example/report.csv).

**What this means for the AI repair loop:**

- "Point-set F1 sliced by `fluent_type` (EDF vs SDF)" — the *point-set* part
  is already done by `compare_ce` (timepoint-based, half-open intervals,
  symmetric).
- The keying by `(name, value)` is exactly the FVP granularity the thesis
  cares about. To slice by EDF/SDF, group these keys by a tag the AI layer
  attaches (the engine has no notion of EDF vs SDF; it's a thesis label).
- **Do not reimplement F1.** Wrap `compare_ce` / `get_micro` / `get_macro`.
- One real wart: `evaluate.py` has its `open(output_file_path, 'w')` block at
  module top level (lines 64+), not inside the `__main__` guard. Harmless for
  CLI use, but means you can't safely `import evaluate` — call the utilities
  directly. Not blocking; flagging in case the AI layer wants to import.

---

## 5. Maritime domain layout

Root: [examples/maritime/](../examples/maritime/).

### 5.1 Rules — `resources/patterns/`

- [`rules.prolog`](../examples/maritime/resources/patterns/rules.prolog) (398 lines) —
  hand-authored event description. Includes `withinArea`, `gap`, `stopped`,
  `lowSpeed`, `changingSpeed`, `highSpeedNearCoast`, `movingSpeed`, `underWay`,
  `drifting`, `anchoredOrMoored`, `tuggingSpeed`/`tugging`, `rendezVous`,
  `trawlSpeed`/`trawlingMovement`/`trawling`, `sarSpeed`/`sarMovement`/`inSAR`,
  `loitering`, `pilotOps`. Mix of EDFs (`initiatedAt`/`terminatedAt`) and SDFs
  (`holdsFor` with interval-algebra bodies). **The canonical
  `highSpeedNearCoast` from CLAUDE.md §3 lives here at lines 71–85.**
- `compiled_rules.prolog` (681 lines) — generated by `auxiliary/compile.sh`.
  Modified on every bash-driver run.
- `alternative_pattern_definitions/` — `rules_allen.prolog` (Allen-style
  formulation, used by the `maritime_allen` app) and
  `rules_inst_speed_change.prolog`.

### 5.2 Declarations & background knowledge — `resources/auxiliary/`

- [`compare.prolog`](../examples/maritime/resources/auxiliary/compare.prolog) —
  helpers: `intDurGreater`/`intDurLess` (duration filters used by SDFs),
  `absoluteAngleDiff`, `fmod`, and **`inRange/3`** — note this is an
  *exclusive* `Var > Min, Var < Max`. (CLAUDE.md flags the boundary-comparator
  pitfall; here is the actual definition.)
- [`loadStaticData.prolog`](../examples/maritime/resources/auxiliary/loadStaticData.prolog) —
  one-file consult-list that pulls in everything below.
- [`staticDataPredicates.prolog`](../examples/maritime/resources/auxiliary/staticDataPredicates.prolog) —
  `areaType/2`, `vesselType/2`, `oneIsTug/2`, `oneIsPilot/2`, `twoAreTugs/2`,
  `draft/2`, `draught/2`.

### 5.3 Vocabulary tables

- `areaIDs/areaTypes.prolog` — six area types:
  `anchorage, fishing, natura, nearCoast, nearCoast5k, nearPorts`.
- `areaIDs/{anchorage,fishing,natura,nearCoast,nearCoast5k,nearPorts}_big_areas.prolog` —
  `bigAreaType/2` facts mapping concrete area IDs to types (1 800+ facts).
- `vesselInformation/`:
  - `vesselStaticInfo.prolog` (~4 800 facts) — `vesselStaticInfo(MMSI, Type, Draft)`.
  - `typeSpeeds.prolog` — `typeSpeed(Type, Min, Max, Avg)` for ~30 vessel types.
  - `vessels.prolog` (~5 000 facts) and `vesselPairs.prolog` (~3 000 facts) —
    note `loadStaticData.prolog` deliberately does **not** consult these:
    dynamic grounding handles them at runtime (see comment lines 18–20).
- `patternsParameters/thresholds.prolog` — 30+ named thresholds
  (`thresholds(<key>, <value>)`) referenced throughout the rules
  (`hcNearCoastMax=5.0`, `loiteringTime=1800`, etc.). Modifying any of these
  is one of the cheapest ways to shift recognised intervals — flag in any
  reproducibility audit.
- `patternsParameters/movingStatus.prolog` — three values: `below, normal, above`.
- `portRelatedData/portStatuses.prolog` — `nearPorts, farFromPorts`.

### 5.4 Dataset — `dataset/csv/`

- `brest_critical.csv` — pipe-delimited, ~16M rows, ~60 days of AIS events.
  Column layout (varies by event type, dispatched on first column):
  - `coord|t|t|MMSI|lon|lat`
  - `velocity|t|t|MMSI|speed|course|heading`
  - `change_in_heading|t|t|MMSI`
  - `gap_start|t|t|MMSI` / `gap_end|t|t|MMSI`
  - `stop_start|...` / `stop_end|...`
  - `slow_motion_start|...` / `slow_motion_end|...`
  - `change_in_speed_start|...` / `change_in_speed_end|...`
  - `entersArea|t|t|MMSI|area_id` / `leavesArea|t|t|MMSI|area_id`
- `dataset_download.txt` — pointer to the original download.

### 5.5 Default execution params (from `defaults.toml` `[maritime]`)

```
window_size = 36000   step = 36000
start_time  = 1443650400   end_time = 1448834400
clock_tick  = 1   input_mode = "csv"   output_mode = "file"
```

= 60 days of 10-hour windows. For AI-layer iteration, override `end_time` to a
single window (`1443686400`) — produces a complete, representative result set
in <1 s, vs ~minutes for the full sweep.

---

## 6. CAVIAR (HAR) layout — extension domain

Root: [examples/caviar/](../examples/caviar/). Same skeleton as maritime.

- Rules: [`resources/patterns/rules.prolog`](../examples/caviar/resources/patterns/rules.prolog).
  CE definitions for `close_24/25/30/34`, `closeSymmetric_30`,
  `activeOrInactivePerson`, `moving`, `meeting`, `greeting1`, `fighting`.
  (These are the FVPs in [scoring/example/report.csv](../execution%20scripts/scoring/example/report.csv)
  — handy reference set.)
- Background: [`resources/auxiliary/pre-processing.prolog`](../examples/caviar/resources/auxiliary/pre-processing.prolog)
  — computes the `distance/3` SDF dynamically per query window.
- Static IDs: `dataset/auxiliary/list-of-ids.prolog` (commented out in
  `defaults.toml`; dynamic grounding handles it).
- Dataset: pipe-delimited CSVs in `dataset/csv/`:
  - `appearance.csv`: `orientation|t|t|angle|id` / `appear|t|t|id` /
    `appearance|t|t|visible|id`.
  - `movementB.csv`: `walking|t|t|true|id` / `coord|t|t|true|id|x|y`.
- Defaults: `window_size = step = 100000`, `clock_tick = 40`, time-points
  rather than UNIX epochs. `dependency_graph_flag = true` (the only example
  that has it on by default).

---

## 7. Surprises / risks worth flagging

1. **Two executor entry points, two different compile contracts.** Bash driver
   recompiles `rules.prolog` every run; CLI consults the pre-compiled file
   directly. If the AI layer mutates `rules.prolog` and goes through the CLI
   without compiling, the new rule is silently ignored. Recommend the AI layer
   target the bash driver (`run_rtec.sh`).
2. **`maritime_allen` is in the bash whitelist but not the CLI whitelist.**
   Unrelated to current work but a real divergence; pick one wrapper and stay
   in it.
3. **`compiled_rules.prolog` is regenerated on every bash run**, hence the
   "M" entries in `git status`. These artifacts are gitignored; the diff is
   noise. Don't commit them.
4. **`evaluate.py` does file I/O at module scope**, so it is not safely
   importable. Use the `utilities/*` modules directly from any AI-layer
   evaluator.
5. **`inRange/3` is strict-exclusive** (`>`, `<`). This is the source of the
   `highSpeedNearCoast` boundary-comparator drift described in CLAUDE.md §3;
   confirmed in [compare.prolog:33–35](../examples/maritime/resources/auxiliary/compare.prolog#L33-L35).
6. **Output filenames embed parameters** — `log-swi-<window>-<step>-<input_mode>-<output_mode>-recognised-intervals.txt`. If the AI layer runs two iterations with different params back-to-back, they will land in different files; if it runs with identical params, the second overwrites the first. Plan a results staging directory per iteration.
7. **CSV is pipe-delimited** in both maritime and caviar. Trivial but worth
   noting before anyone reaches for `pandas.read_csv(...)` with the default
   comma separator.
8. **Scoring is point-set timepoint F1, half-open intervals.** The off-by-one
   risk from CLAUDE.md §3 is real: a one-tick endpoint drift across N intervals
   costs N timepoints. The metric will surface that drift — but only if
   ground-truth files use the same half-open convention.

---

## STOP — handing off to next phase

Bare engine runs cleanly. Executor and scoring seams are well-defined and
reusable as-is. The AI layer can wrap `run_rtec.sh` (executor) and the
`scoring/utilities/` modules (oracle) without engine modifications. No code
under `src/` or `execution scripts/` was changed during this pass.
