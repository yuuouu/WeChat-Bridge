.PHONY: install-dev lint format format-check test check

PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
PYTEST := $(VENV)/bin/pytest

$(PY):
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r app/requirements.txt -r requirements-dev.txt

install-dev: $(PY)

lint: $(PY)
	$(RUFF) check app/ tests/ examples/

format: $(PY)
	$(RUFF) format app/ tests/ examples/

format-check: $(PY)
	$(RUFF) format --check app/ tests/ examples/

test: $(PY)
	$(PYTEST) tests/ -v --cov=app --cov-report=term-missing

check: lint format-check test
