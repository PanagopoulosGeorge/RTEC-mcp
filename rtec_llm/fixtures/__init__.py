"""Fixtures — annotation-driven test cases for the repair loop and evaluation.

A fixture is the input contract that replaced the abandoned "Approach 1"
(expert-written reference rules — CLAUDE.md §6). It carries the NL spec, a
pointer to the event stream, the EDF/SDF tag, and the ground-truth intervals
the engine output is scored against.

Dependency rules (see ``docs/ARCHITECTURE.md`` §4):
- ``fixtures/*`` may import ``types`` and ``domain``.
- ``fixtures/*`` must NOT import ports, adapters, generation, repair, query,
  evaluation, or the CLI.
- ``repair/*``, ``evaluation/*``, and ``cli.py`` may import ``fixtures``
  (they load test cases); ``generation/*`` must NOT.
"""

from rtec_llm.fixtures.loader import load_fixture, load_fixtures
from rtec_llm.fixtures.schema import FixtureSpec
from rtec_llm.fixtures.validate import unknown_symbols

__all__ = ["FixtureSpec", "load_fixture", "load_fixtures", "unknown_symbols"]
