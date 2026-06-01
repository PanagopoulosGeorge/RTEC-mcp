"""Make-runnable fixture validation: ``python -m rtec_llm.fixtures.check``.

Loads every seeded fixture, loads its domain ``Vocabulary``, and fails (exit 1)
if any fixture references a symbol absent from that vocabulary — the executable
form of the "never invent vocabulary" invariant (CLAUDE.md §5 invariant 3).
Wired into ``make check-fixtures``. Engine-independent (no swipl).
"""

from __future__ import annotations

import sys

from rtec_llm.domain import Vocabulary, load_domain
from rtec_llm.fixtures.loader import load_fixtures
from rtec_llm.fixtures.validate import unknown_symbols


def main() -> int:
    fixtures = load_fixtures()
    if not fixtures:
        print("no fixtures found under rtec_llm/fixtures/data/")
        return 0

    vocab_cache: dict[str, Vocabulary] = {}
    total_problems = 0
    for spec in fixtures:
        vocab = vocab_cache.get(spec.domain)
        if vocab is None:
            vocab = load_domain(spec.domain)
            vocab_cache[spec.domain] = vocab
        problems = unknown_symbols(spec, vocab)
        tag = f"{spec.domain}:{spec.fluent_name} ({spec.fluent_type})"
        if problems:
            total_problems += len(problems)
            print(f"FAIL {tag}")
            for problem in problems:
                print(f"     - {problem}")
        else:
            print(f"OK   {tag}")

    print(f"\n{len(fixtures)} fixture(s), {total_problems} unknown-symbol problem(s).")
    return 1 if total_problems else 0


if __name__ == "__main__":
    sys.exit(main())
