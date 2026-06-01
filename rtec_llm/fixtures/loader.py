"""Fixture loader — YAML files → ``FixtureSpec`` instances.

Reads the on-disk fixture format (``data/<domain>/<fluent>.yaml``) into typed
``FixtureSpec`` objects, resolving the event-stream and ground-truth paths to
absolute paths. ``event_stream_ref`` is resolved against the repo root;
``ground_truth_file`` against the fixture file's own directory (GT files are
co-located with the fixture).

A light half-open sanity check (``end > start`` on every interval, by regex) is
applied to the ground-truth file at load time — without importing the scoring
parser, to keep the ``fixtures/`` import boundary (``types`` + ``domain`` only;
ARCHITECTURE.md §4). It catches a corrupted/hand-edited GT before it silently
poisons every F1 (ARCHITECTURE.md §1).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from rtec_llm.fixtures.schema import FixtureSpec
from rtec_llm.types import FluentType, Window

_DATA_DIR = Path(__file__).resolve().parent / "data"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERVAL_RE = re.compile(r"\((\d+),(\d+)\)")


def load_fixture(path: Path) -> FixtureSpec:
    """Load and type a single fixture YAML file.

    Raises:
        FileNotFoundError: if the fixture, its event stream, or its ground-truth
            file is missing.
        ValueError: on a malformed field or a non-half-open ground-truth interval.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a YAML mapping at the top level")

    event_stream_ref = _resolve(str(raw["event_stream_ref"]), base=_REPO_ROOT)
    ground_truth_file = _resolve(str(raw["ground_truth_file"]), base=path.parent)
    if not event_stream_ref.is_file():
        raise FileNotFoundError(f"{path}: event_stream_ref not found: {event_stream_ref}")
    if not ground_truth_file.is_file():
        raise FileNotFoundError(f"{path}: ground_truth_file not found: {ground_truth_file}")
    _check_half_open(ground_truth_file)

    return FixtureSpec(
        domain=str(raw["domain"]),
        fluent_name=str(raw["fluent_name"]),
        fluent_type=_fluent_type(raw["fluent_type"]),
        nl_spec=str(raw["nl_spec"]).strip(),
        event_stream_ref=event_stream_ref,
        ground_truth_file=ground_truth_file,
        window=_window(raw["window"]),
        domain_facts=tuple(str(f) for f in raw.get("domain_facts", [])),
        prerequisite_fluents=tuple(str(f) for f in raw.get("prerequisite_fluents", [])),
    )


def load_fixtures(domain: str | None = None) -> tuple[FixtureSpec, ...]:
    """Load every fixture under ``data/`` (optionally restricted to one domain)."""
    root = _DATA_DIR / domain if domain else _DATA_DIR
    return tuple(load_fixture(p) for p in sorted(root.rglob("*.yaml")))


def _resolve(ref: str, *, base: Path) -> Path:
    candidate = Path(ref)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _window(raw: Any) -> Window:
    if not isinstance(raw, dict):
        raise ValueError(f"window must be a mapping, got {type(raw).__name__}")
    return Window(
        start_time=int(raw["start_time"]),
        end_time=int(raw["end_time"]),
        window_size=int(raw["window_size"]),
        step=int(raw["step"]),
    )


def _fluent_type(value: Any) -> FluentType:
    text = str(value)
    if text not in ("EDF", "SDF"):
        raise ValueError(f"fluent_type must be 'EDF' or 'SDF', got {text!r}")
    return text  # type: ignore[return-value]  # narrowed by the guard above


def _check_half_open(path: Path) -> None:
    for start, end in _INTERVAL_RE.findall(path.read_text(encoding="utf-8")):
        if int(end) <= int(start):
            raise ValueError(
                f"{path}: ground-truth interval ({start},{end}) is not half-open "
                f"[s, e) with e > s — every F1 would be systematically wrong "
                f"(ARCHITECTURE.md §1)."
            )
