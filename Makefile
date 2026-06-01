.PHONY: install lint test smoke-test check-fixtures clean help

# Python 3.12 is required (see CLAUDE.md §6 Engineering conventions).
PY ?= python3.12
VENV := .venv
BIN := $(VENV)/bin

help:
	@echo "Targets:"
	@echo "  install     Create .venv (Python 3.12) and install rtec_llm[dev] + pre-commit"
	@echo "  lint        ruff check + ruff format --check + mypy strict on rtec_llm/"
	@echo "  test            pytest on rtec_llm/"
	@echo "  smoke-test      Run the engine adapter end-to-end against real swipl"
	@echo "  check-fixtures  Fail if any fixture references a symbol absent from its Vocabulary"
	@echo "  clean           Remove caches and the venv"

$(VENV)/bin/python:
	@if command -v uv >/dev/null 2>&1; then \
		uv venv $(VENV) -p 3.12; \
	else \
		$(PY) -m venv $(VENV); \
	fi

install: $(VENV)/bin/python
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install --python $(BIN)/python -e ".[dev]"; \
	else \
		$(BIN)/pip install -U pip && $(BIN)/pip install -e ".[dev]"; \
	fi
	$(BIN)/pre-commit install

lint:
	$(BIN)/ruff check rtec_llm
	$(BIN)/ruff format --check rtec_llm
	$(BIN)/mypy rtec_llm

test:
	$(BIN)/pytest rtec_llm tests --ignore=tests/test_engine_smoke.py

smoke-test:
	@echo ">> Running engine-adapter smoke tests against real swipl (no mocks on the oracle)"
	$(BIN)/pytest tests/test_engine_smoke.py -v

check-fixtures:
	@echo ">> Validating fixtures against their domain vocabularies (never invent vocabulary)"
	$(BIN)/python -m rtec_llm.fixtures.check

clean:
	rm -rf $(VENV) .mypy_cache .ruff_cache .pytest_cache
	find rtec_llm -type d -name __pycache__ -exec rm -rf {} +
