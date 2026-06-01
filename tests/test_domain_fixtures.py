"""Unit tests for the domain vocabulary loader and the fixture contract.

Engine-independent (no swipl). Proves three things the rest of the loop is
graded against:
* the MSA/HAR vocabularies load into typed ``Vocabulary`` objects with the
  expected symbols, EDF/SDF tags, and the ``inRange`` exclusivity note;
* the fixture validator catches references to symbols absent from a vocabulary
  (the "never invent vocabulary" guard) and passes the seeded fixtures;
* the seeded ground-truth files are consumable by the P3 scoring adapter — i.e.
  the fixture → GT-file → oracle seam actually connects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Importing the scoring adapter puts `execution scripts/scoring/` on sys.path,
# exposing `parser` for the GT round-trip check below (same trick as test_scoring).
import rtec_llm.adapters.scoring.rtec_scoring as rs
from rtec_llm.domain import Vocabulary, load_domain
from rtec_llm.fixtures import FixtureSpec, load_fixtures, unknown_symbols
from rtec_llm.fixtures.check import main as check_main
from rtec_llm.types import ExecutionResult, Window

_DUMMY = Path("/dev/null")
_DUMMY_WINDOW = Window(start_time=0, end_time=1, window_size=1, step=1)


# --------------------------------------------------------------------------- #
# Domain vocabulary
# --------------------------------------------------------------------------- #


def test_msa_vocabulary_loads_expected_symbols() -> None:
    v = load_domain("msa")
    assert v.name == "msa"
    # A spread of events / fluents / BK / value domains from examples/maritime/.
    assert {"entersArea", "leavesArea", "velocity", "gap_start"} <= v.event_names()
    assert "proximity" in v.input_fluent_names()
    assert {"withinArea", "highSpeedNearCoast", "loitering", "rendezVous"} <= v.output_fluent_names()
    assert "thresholds" in v.predicate_functors()
    assert "loiteringTime" in v.threshold_keys()
    assert v.value_domain("areaType") == (
        "anchorage",
        "fishing",
        "natura",
        "nearCoast",
        "nearCoast5k",
        "nearPorts",
    )


def test_msa_fluent_type_tags_and_arity() -> None:
    v = load_domain("msa")
    assert v.fluent("withinArea").fluent_type == "EDF"  # type: ignore[union-attr]
    assert v.fluent("loitering").fluent_type == "SDF"  # type: ignore[union-attr]
    within = v.fluent("withinArea")
    assert within is not None
    assert within.arity == 2  # derived from arg_names, never read from YAML
    assert within.arg_names == ("vessel", "areaType")
    # movingSpeed is a multi-valued EDF.
    moving = v.fluent("movingSpeed")
    assert moving is not None
    assert moving.values == ("below", "normal", "above")


def test_msa_records_inrange_exclusivity_note() -> None:
    """The highSpeedNearCoast boundary pitfall must be recorded, not lost."""
    v = load_domain("msa")
    in_range = next(b for b in v.background_knowledge if b.name == "inRange")
    assert in_range.arity == 3
    assert in_range.note is not None and "EXCLUSIVE" in in_range.note


def test_true_value_is_a_string_not_a_bool() -> None:
    """The bare-`true` YAML trap is dodged — fluent values stay Prolog strings."""
    within = load_domain("msa").fluent("withinArea")
    assert within is not None
    assert within.values == ("true",)
    assert isinstance(within.values[0], str)


def test_har_stub_loads() -> None:
    v = load_domain("har")
    assert v.name == "har"
    assert "person" in v.output_fluent_names()
    assert {"close_24", "close_30"} <= v.output_fluent_names()


def test_unknown_domain_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_domain("does_not_exist")


# --------------------------------------------------------------------------- #
# Fixtures + validation
# --------------------------------------------------------------------------- #


def test_seeded_fixtures_load_and_span_difficulty() -> None:
    fixtures = load_fixtures("msa")
    by_name = {f.fluent_name: f for f in fixtures}
    assert {"withinArea", "highSpeedNearCoast", "loitering", "rendezVous"} <= set(by_name)
    # The difficulty spread the thesis cares about: EDFs and interval-algebra SDFs.
    assert by_name["withinArea"].fluent_type == "EDF"
    assert by_name["loitering"].fluent_type == "SDF"
    assert "withinArea" in by_name["highSpeedNearCoast"].prerequisite_fluents


def test_seeded_fixtures_are_fully_grounded() -> None:
    """Every seeded fixture references only symbols in its vocabulary."""
    for spec in load_fixtures("msa"):
        vocab = load_domain(spec.domain)
        assert unknown_symbols(spec, vocab) == [], spec.fluent_name


def test_validator_flags_invented_symbols() -> None:
    vocab = load_domain("msa")
    bad = FixtureSpec(
        domain="msa",
        fluent_name="teleporting",  # invented fluent
        fluent_type="SDF",
        nl_spec="nonsense",
        event_stream_ref=_DUMMY,
        ground_truth_file=_DUMMY,
        window=_DUMMY_WINDOW,
        domain_facts=("frobnicate(v1).", "thresholds(madeUpKey, 5)."),
        prerequisite_fluents=("wormhole",),
    )
    problems = "\n".join(unknown_symbols(bad, vocab))
    assert "teleporting" in problems
    assert "wormhole" in problems
    assert "frobnicate" in problems
    assert "madeUpKey" in problems


def test_validator_flags_fluent_type_mismatch() -> None:
    vocab = load_domain("msa")
    mismatched = FixtureSpec(
        domain="msa",
        fluent_name="withinArea",  # vocabulary tags this EDF
        fluent_type="SDF",
        nl_spec="x",
        event_stream_ref=_DUMMY,
        ground_truth_file=_DUMMY,
        window=_DUMMY_WINDOW,
    )
    problems = "\n".join(unknown_symbols(mismatched, vocab))
    assert "disagrees" in problems


def test_check_main_passes_on_seeded_fixtures() -> None:
    assert check_main() == 0


# --------------------------------------------------------------------------- #
# Fixture ↔ P3 oracle seam
# --------------------------------------------------------------------------- #


def test_ground_truth_files_are_scorer_consumable() -> None:
    """Each seeded GT file parses via P3's parser and scores through the oracle.

    An empty prediction against the GT must yield F1 0.0 with all ground-truth
    timepoints counted as false negatives, the fluent_type passing through — the
    fixture → GT-file → ScoringPort wiring the repair loop depends on.
    """
    scorer = rs.RtecScorer()
    for spec in load_fixtures("msa"):
        parsed = rs.parser.parse_file(spec.ground_truth_file)
        assert parsed, f"{spec.fluent_name}: GT file parsed empty"
        assert any(name == spec.fluent_name for (name, _value) in parsed)

        result = scorer.score(ExecutionResult(recognised=()), spec.ground_truth_file, spec.fluent_type)
        assert result.fluent_type == spec.fluent_type
        assert result.tp == 0
        assert result.fn > 0
        assert result.f1 == 0.0