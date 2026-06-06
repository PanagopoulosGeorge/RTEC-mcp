# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

RTEC is a Run-Time Event Calculus reasoner written in Prolog, optimised for stream reasoning over time-stamped events. The core reasoner is Prolog code in `src/`; everything else (CLI, bash scripts, TOML defaults, example apps) is plumbing that compiles an event description, feeds it a stream of input events, and runs `eventRecognition/2` over sliding windows. Target Prolog is SWI-Prolog 8.2+ (YAP is also supported). See `RTEC_manual.pdf` for the formal grammar of event descriptions and the windowing model.

## Two ways to run RTEC

These are independent entry points; they do *not* share configuration. Pick one and stick with it.

### 1. Bash script + `defaults.toml` (the manual path)

Run from inside `execution scripts/`:

```bash
cd "execution scripts"
./run_rtec.sh --app=toy                    # default params from defaults.toml
./run_rtec.sh --app=toy --window-size=20   # override one param (TOML keys with - instead of _)
```

`defaults.toml` is the single source of truth for per-application defaults (window/step/start/end, input mode, paths to event description and background knowledge, results dir, dependency-graph flags). The applications recognised by `run_rtec.sh` are exactly the TOML table names: `toy`, `maritime`, `maritime_allen`, `voting`, `netbill`, `caviar`, `ctm`. Adding a new app to this path = adding a new TOML table.

`run_rtec.sh` calls `auxiliary/compile.sh` (which invokes `src/compiler.prolog` to produce `compiled_rules.prolog` alongside the rules file, and optionally a Graphviz dependency graph) and then launches `swipl` on `continuousQueries.prolog`.

### 2. Python CLI (`RTEC2`)

Install once:

```bash
uv venv .venv && source .venv/bin/activate
bash install.sh                # prefers `uv pip install .`, falls back to pip
RTEC2 --use-case voting --path examples/voting
RTEC2 --help                   # full flag list
```

**Critical install behaviour — read this before touching `install.sh` or `setup.py`.** The setuptools package is named `RTECv2` but the repo's source lives in `src/` and `execution scripts/` at the repo root. `install.sh` *temporarily* creates `RTECv2/`, moves `src/` → `RTECv2/src` and `execution scripts/` → `RTECv2/scripts`, runs the install, then a trap restores the original layout. It also stashes `pyproject.toml`/`uv.lock` as `.install.bak` during the install to avoid setuptools conflicts. If the script is interrupted, you may need to manually undo: move `RTECv2/src` back to `src`, `RTECv2/scripts` back to `execution scripts`, restore the `.install.bak` files, and remove the `RTECv2/` directory.

The CLI (`execution scripts/RTEC2_cli.py`, packaged as `RTECv2/scripts/RTEC2_cli.py`) discovers Prolog files and CSV/Prolog inputs by *convention* under `--path`:
- event description: `<path>/resources/patterns/compiled_rules.prolog` + everything in `<path>/resources/auxiliary/*.prolog` and `<path>/dataset/auxiliary/*.prolog`
- input providers: `<path>/dataset/csv/*.csv` (csv mode) or `<path>/dataset/prolog/*.prolog` (dynamic_predicates mode)

The `--use-case` flag is validated against a hard-coded enum (`toy, caviar, maritime, voting, netbill, ctm`); adding a new app here = editing `use_case_enum` in the CLI **and** following the directory convention above.

Uninstall with `pip uninstall RTEC2`.

## Architecture: how a run flows end-to-end

1. **Compile event description.** `src/compiler.prolog` reads user-authored Prolog rules + declarations (`event/1`, `simpleFluent/1`, `sDFluent/1`, `inputEntity/1`, `outputEntity/1`, `index/2`, `initiatedAt/4`, `terminatedAt/4`, `holdsFor/2`, `happensAt/2`, etc.) and emits `compiled_rules.prolog` next to the input. The compiler analyses fluent dependencies (SCCs, cyclic dependencies) and can optionally rewrite "translatable" simple fluents as statically determined fluents (`--definition-optimisation`).
2. **Load & assert.** `continuousQueries.prolog` consults the compiled rules + background knowledge, then uses `src/data loader/dataLoader.prolog` (CSV) or input from named pipes / Unix sockets to assert input events.
3. **Window loop.** `src/RTEC.prolog` drives the recognition loop: for each query time `Qi`, `eventRecognition(Qi, WM)` processes events that fall in `(Qi-WM, Qi]`. Processing order is governed by `cachingOrder/2` (set per-entity by the compiler). Intervals from prior windows are cached as `holdsForProcessedIE`, `holdsForProcessedSimpleFluent`, `holdsForProcessedSDFluent`; `*_Extension` lists carry forward the tail before the window so amalgamation produces correct maximal intervals.
4. **Entity kinds — the type system you must respect when reading/writing rules.**
   - *Events* (`event/1`): instantaneous, defined via `happensAt`.
   - *Simple fluents* (`simpleFluent/1`): durative, defined via `initiatedAt/2,4` and `terminatedAt/2,4` (i.e., with inertia).
   - *Statically determined fluents* (`sDFluent/1`): durative, defined purely via interval-manipulation constructs (`holdsFor/2`, union/intersection/complement of other intervals — see `src/allen.prolog` and `src/utilities/`). SD fluents are **not** defined with `holdsAt`.
   - *Input vs output* (`inputEntity/1` / `outputEntity/1`): orthogonal to the kind above; input entities come from the stream, output entities are derived.
5. **Input modes.** `csv` (batch from files), `fifo` (live from named pipes — `run_rtec.sh` will create FIFOs and stream files into them if you point it at plain files), `socket` (Unix domain socket). In live modes the loop sleeps `window_size` seconds between query times to wait for in-window events to arrive; `stream_rate` lets you replay historical data faster than wall clock.

Key files at a glance:
- `src/RTEC.prolog` — main recognition loop, predicate API documented in the top-of-file comment.
- `src/compiler.prolog` — `compileED/2,4`; transforms user rules into the indexed, dependency-aware form RTEC actually runs.
- `src/processEvents.prolog`, `processSimpleFluents.prolog`, `processSDFluents.prolog` — per-kind processing.
- `src/allen.prolog`, `src/utilities/interval-manipulation.prolog`, `src/utilities/amalgamate-periods.prolog` — interval algebra used by SD fluent definitions.
- `src/dynamic grounding/dynamicGrounding.prolog` — optional ground-on-demand mode (CLI flag `--dynamic-grounding True`).
- `src/timeoutTreatment.prolog` — handling of events with delayed effects.
- `src/data loader/dataLoader.prolog`, `manualCSVReader.prolog` — CSV ingestion.
- `execution scripts/handleApplication.prolog`, `continuousQueries.prolog`, `logger.prolog` — orchestration consulted by both entry points.

## Example app convention

Every directory under `examples/<app>/` follows the same layout, and both entry points depend on it:

```
examples/<app>/
  resources/
    patterns/    <- rules.prolog (source) + compiled_rules.prolog (generated)
    auxiliary/   <- compare predicates, helpers consulted before reasoning
    graphs/      <- generated dependency_graph.{dot,png}
  dataset/
    csv/         <- input event streams (csv mode)
    prolog/      <- input event streams (dynamic_predicates mode)
    auxiliary/   <- background knowledge (e.g., entity domains, static facts)
  results/       <- written at runtime: log + intervals per window
```

Breaking this convention silently breaks the Python CLI's discovery logic. The bash path is more permissive — you spell out paths in the TOML.

## Tests

Each test suite has its own runner; there is no top-level test command.

```bash
# Core RTEC tests (uses both unit tests and end-to-end scenarios)
cd unit-tests/RTEC-tests && ./runallRTECtests-SWI.sh         # or runallRTECtests-YAP.sh

# Compiler tests (compile rules.prolog and diff against rules_compiled_t.prolog)
cd unit-tests/compiler-tests && ./runallcompilertests-SWI.sh # or ...YAP.sh

# Allen-interval algebra tests
cd unit-tests/allen-tests && ./run_tests.sh

# Data loader tests
cd unit-tests/data-loader-tests && ./runalldataLoadertests.sh
```

To run a single RTEC test file: `swipl -q -l unit-tests/RTEC-tests/tests/<file>.prolog -g runtests_swi -- unit-tests/RTEC-tests/tests/<file>.prolog`.

## Optional tooling

- **GraphViz `dot`** is needed only if you set `dependency_graph_flag = true` in TOML or pass `--dependency-graph` to `compile.sh`. The compile step calls `dot -T png` to render `dependency_graph.png`.
- **YAP Prolog** is supported as an alternative to SWI; pass `--prolog yap` to the CLI, or use the `*-YAP.sh` test runners. YAP needs `-s 0 -h 0 -t 0` startup flags (handled for you by the CLI).
