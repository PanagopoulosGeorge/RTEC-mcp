"""``EnginePort`` adapter that runs the REAL RTEC engine via SWI-Prolog.

This is the sole correctness signal for rule behaviour (CLAUDE.md §3, §5). It is
deliberately *not* mocked: every result here comes from ``swipl`` executing the
compiled event description against an input stream.

Invocation strategy — the two steps ``execution scripts/run_rtec.sh`` performs
internally, run directly so we can (a) separate compile-time from run-time
failures into distinct typed errors and (b) avoid the driver's ``sleep 10`` on a
failed compile, which would tax the repair loop:

1. ``auxiliary/compile.sh --event-description=<tmp>/rules.prolog --no-events``
   compiles the (temp) event description to ``<tmp>/compiled_rules.prolog``.
   The bash driver adds ``--no-events`` whenever ``include_input`` is off, which
   is the default for every packaged app (see ``defaults.toml`` + ``run_rtec.sh``).
2. ``swipl -q -l continuousQueries.prolog -g continuousQueries(<app>, <params>)
   -t halt`` runs recognition and writes the recognised-intervals file into a
   temp results directory.

Both steps run with ``cwd = "execution scripts"`` because the engine resolves
``continuousQueries.prolog``, ``../src/...`` and the compiler by relative path
from there — exactly as the bash driver does. Nothing under ``src/`` or
``execution scripts/`` is modified (CLAUDE.md §5 invariant 1); we only write to a
private temp directory and clean it up.

Per ``pyproject.toml`` the engine adapter depends on **stdlib + subprocess only**.
RTEC's recognised-intervals output is parsed here with a small regex parser;
the *scoring* adapter is the component that wraps
``execution scripts/scoring/utilities/`` for the F1 oracle.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from rtec_llm.types import (
    EngineError,
    ExecutionResult,
    FluentValuePair,
    Interval,
    RecognisedFluent,
    Window,
)

# One ``recognitions(predictions, <name>, [[<args>], <value>], [<intervals>]).`` line.
# Args are comma-separated and may be empty; values are simple atoms (no ``]``).
_RECOGNITION_RE = re.compile(
    r"^recognitions\(predictions,"
    r"([^,]+),"  # fluent name
    r"\[\[([^\]]*)\],"  # [[ arg1,arg2,... ]
    r"([^\]]+)\],"  # value ]
    r"\[(.*)\]\)\.$"  # [ (s,e),(s,e),... ] ).
)
_INTERVAL_RE = re.compile(r"\((\d+),(\d+)\)")
# A ``some/path/rules.prolog:LINE`` pointer in compiler / runtime stderr.
_SOURCE_RE = re.compile(r"([^\s:()]+\.prolog):(\d+)")

_STDERR_CLIP = 4000


class RtecSubprocessEngine:
    """Single-run RTEC executor backed by a real ``swipl`` subprocess.

    One instance is bound to a packaged RTEC application (``app``), which the
    engine uses to select input parsing, dynamic-grounding behaviour, and
    parameter defaults. The rules, event stream, window, and static data vary
    per :meth:`run` call. Satisfies :class:`rtec_llm.ports.engine.EnginePort`.

    Args:
        app: RTEC application name (e.g. ``"toy"``, ``"maritime"``). Must be a
            table in ``execution scripts/defaults.toml`` / known to
            ``handleApplication.prolog``.
        repo_root: Repository root. Defaults to the checkout this module lives
            in. ``execution scripts/`` must exist beneath it.
        swipl: SWI-Prolog executable name or path.
        timeout_s: Per-subprocess wall-clock timeout (applied to both the
            compile step and the recognition step).
    """

    def __init__(
        self,
        app: str,
        *,
        repo_root: Path | None = None,
        swipl: str = "swipl",
        timeout_s: float = 600.0,
    ) -> None:
        self._app = app
        self._swipl = swipl
        self._timeout_s = timeout_s
        root = repo_root if repo_root is not None else Path(__file__).resolve().parents[3]
        self._exec_scripts = root / "execution scripts"
        self._compile_sh = self._exec_scripts / "auxiliary" / "compile.sh"
        if not self._compile_sh.is_file():
            raise FileNotFoundError(
                f"RTEC compiler script not found at {self._compile_sh}. "
                "Pass repo_root pointing at a checkout that contains 'execution scripts/'."
            )

    def run(
        self,
        *,
        rules: str,
        declarations: str | None,
        event_stream: Path,
        window: Window,
        static_data: tuple[Path, ...],
    ) -> ExecutionResult:
        """Run RTEC once; see :class:`rtec_llm.ports.engine.EnginePort`."""
        work = Path(tempfile.mkdtemp(prefix="rtec_engine_"))
        try:
            results_dir = work / "results"
            results_dir.mkdir()
            rules_file = work / "rules.prolog"
            event_description = f"{declarations}\n\n{rules}" if declarations else rules
            rules_file.write_text(event_description, encoding="utf-8")

            # 1. Compile the event description (compile-time errors land here).
            try:
                compile_proc = subprocess.run(
                    [
                        "bash",
                        str(self._compile_sh),
                        f"--event-description={rules_file}",
                        "--no-events",
                    ],
                    cwd=self._exec_scripts,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                msg = f"compilation exceeded {self._timeout_s}s\n{_as_text(exc.stderr)}"
                return _failed("timeout", msg)
            if compile_proc.returncode != 0:
                # NOTE: compiled_rules.prolog may still exist (stale/partial) after a
                # failed compile, so the exit code — not file presence — is the signal.
                return _failed(
                    "compile_error",
                    compile_proc.stderr or compile_proc.stdout,
                    source=_extract_source(compile_proc.stderr),
                )
            compiled = work / "compiled_rules.prolog"
            if not compiled.is_file():
                return _failed(
                    "compile_error",
                    "compiler reported success but produced no compiled_rules.prolog",
                )

            # 2. Run recognition (run-time errors land here).
            goal = self._build_goal(compiled, event_stream, window, results_dir, static_data)
            start = time.monotonic()
            try:
                run_proc = subprocess.run(
                    [self._swipl, "-q", "-l", "continuousQueries.prolog", "-g", goal, "-t", "halt"],
                    cwd=self._exec_scripts,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                wall = int((time.monotonic() - start) * 1000)
                msg = f"recognition exceeded {self._timeout_s}s\n{_as_text(exc.stderr)}"
                return _failed("timeout", msg, wall_time_ms=wall)
            wall = int((time.monotonic() - start) * 1000)

            if run_proc.returncode != 0:
                return _failed(
                    "runtime_error",
                    run_proc.stderr or run_proc.stdout,
                    source=_extract_source(run_proc.stderr),
                    wall_time_ms=wall,
                )

            # 3. Locate the recognised-intervals file (fresh dir -> exactly one).
            outputs = sorted(results_dir.glob("*recognised-intervals.txt"))
            if not outputs:
                return _failed(
                    "runtime_error",
                    "RTEC exited 0 but wrote no recognised-intervals file. stderr:\n"
                    + _clip(run_proc.stderr),
                    wall_time_ms=wall,
                )

            # 4. Parse into typed intervals (parse failures land here).
            try:
                recognised, bad_lines = _parse_recognitions(outputs[0])
            except OSError as exc:
                return _failed(
                    "parse_error", f"could not read {outputs[0].name}: {exc}", wall_time_ms=wall
                )

            errors: list[EngineError] = []
            if bad_lines:
                errors.append(
                    EngineError(
                        kind="parse_error",
                        message="unparseable recognition line(s):\n" + "\n".join(bad_lines[:5]),
                    )
                )
            if not recognised:
                errors.append(
                    EngineError(
                        kind="empty_output",
                        message="RTEC produced no recognised intervals for these rules and window.",
                    )
                )
            return ExecutionResult(recognised=recognised, errors=tuple(errors), wall_time_ms=wall)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _build_goal(
        self,
        compiled: Path,
        event_stream: Path,
        window: Window,
        results_dir: Path,
        static_data: tuple[Path, ...],
    ) -> str:
        """Build the ``continuousQueries/2`` goal, mirroring ``set_prolog_command``.

        The compiled rules are appended last to ``event_description_files`` (as the
        bash driver does); ``input_mode``/``output_mode`` are fixed to ``csv``/
        ``file``; ``clock_tick`` and ``stream_rate`` are left to the app defaults.
        """
        files = [*(_pl_atom(p) for p in static_data), _pl_atom(compiled)]
        params = (
            f"event_description_files=[{','.join(files)}],"
            f"window_size={window.window_size},"
            f"step={window.step},"
            f"start_time={window.start_time},"
            f"end_time={window.end_time},"
            f"input_mode=csv,"
            f"input_providers=[{_pl_atom(event_stream)}],"
            f"output_mode=file,"
            f"results_directory={_pl_atom(results_dir)}"
        )
        return f"continuousQueries({self._app},[{params}])"


def _failed(
    kind: str,
    message: str | None,
    source: str | None = None,
    *,
    wall_time_ms: int | None = None,
) -> ExecutionResult:
    """An ``ExecutionResult`` carrying a single engine error and no intervals."""
    return ExecutionResult(
        recognised=(),
        errors=(EngineError(kind=kind, message=_clip(message), source=source),),
        wall_time_ms=wall_time_ms,
    )


def _parse_recognitions(path: Path) -> tuple[tuple[RecognisedFluent, ...], list[str]]:
    """Parse a recognised-intervals file into typed fluents (+ any bad lines).

    Intervals for the same ``(fluent, value, args)`` across multiple windows are
    concatenated and coalesced (overlapping/adjacent half-open intervals merge),
    matching the cross-window temporal union of the scoring layer's parser.
    """
    grouped: dict[tuple[FluentValuePair, tuple[str, ...]], list[tuple[int, int]]] = {}
    order: list[tuple[FluentValuePair, tuple[str, ...]]] = []
    bad: list[str] = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if "recognitions(" not in line:
            continue
        match = _RECOGNITION_RE.match(line)
        if match is None:
            bad.append(line)
            continue
        name, args_str, value, intervals_str = match.groups()
        fvp = FluentValuePair(name=name.strip(), value=value.strip())
        args = tuple(a.strip() for a in args_str.split(",")) if args_str.strip() else ()
        intervals = [(int(s), int(e)) for s, e in _INTERVAL_RE.findall(intervals_str)]
        key = (fvp, args)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].extend(intervals)

    recognised = tuple(
        RecognisedFluent(
            fluent=fvp,
            args=args,
            intervals=tuple(Interval(s, e) for s, e in _coalesce(grouped[(fvp, args)])),
        )
        for fvp, args in order
    )
    return recognised, bad


def _coalesce(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent half-open intervals (matches ``temporal_union``)."""
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _pl_atom(value: Path) -> str:
    """Render a path as a single-quoted Prolog atom with absolute resolution."""
    text = str(value.resolve()).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _extract_source(stderr: str | None) -> str | None:
    """Reduce a ``/tmp/.../rules.prolog:42:5`` stderr pointer to ``rules.prolog:42``."""
    if not stderr:
        return None
    match = _SOURCE_RE.search(stderr)
    if match is None:
        return None
    return f"{Path(match.group(1)).name}:{match.group(2)}"


def _clip(text: str | None, limit: int = _STDERR_CLIP) -> str:
    """Trim engine output to a sane length while surfacing it (never swallow it)."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...(truncated)"


def _as_text(value: str | bytes | None) -> str:
    """Decode partial subprocess output (``TimeoutExpired`` exposes it as bytes)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value
