# KaCRM source-deployment development checks.
.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PYTEST ?= $(PYTHON) -m pytest
UV ?= uv
BUN ?= bun
NPM ?= npm

# Legacy dev-server wiring tests are excluded, as documented in the READMEs.
PYTEST_IGNORE := --ignore=tests/hermes_cloud/test_dev_server.py

.PHONY: help install test test-backend test-frontend build-frontend lint lint-sh lint-data ci

help:
	@echo "KaCRM development commands (source deployment)"
	@echo ""
	@echo "  make install         install project runtime + dev dependencies"
	@echo "  make test            run backend and frontend tests"
	@echo "  make test-backend    run cloud tests without live model evaluations"
	@echo "  make test-frontend   run Bun frontend tests"
	@echo "  make build-frontend  type-check and build the web interface"
	@echo "  make lint            check shell syntax and YAML/JSON configuration"
	@echo "  make ci              tests + configuration checks + frontend build"
	@echo ""
	@echo "  Create .venv first; install Hermes and runtime extras per the deployment guide."
	@echo "  Backend tests require an isolated test environment; known failures are not suppressed."
	@echo "  Override PYTHON, UV, BUN or NPM to select your installed tools."

install:
	$(UV) pip install --python $(PYTHON) -e '.[dev]'

test: test-backend test-frontend

test-backend:
	PYTHONPATH="$(CURDIR)/hermes:$(CURDIR)" $(PYTEST) tests/hermes_cloud/ \
	  --import-mode=importlib $(PYTEST_IGNORE) -m "not llm_eval" -q

test-frontend:
	cd frontend && $(BUN) test

build-frontend:
	cd frontend && $(NPM) run build

lint: lint-sh lint-data

lint-sh:
	@for f in scripts/*.sh; do \
	  [ -f "$$f" ] || continue; \
	  bash -n "$$f" || exit 1; \
	done

lint-data:
	$(PYTHON) -c 'import json, pathlib, yaml; files = [p for root in ("scripts", "monitoring") for p in pathlib.Path(root).rglob("*") if p.suffix in (".json", ".yml", ".yaml")]; [(json.loads(p.read_text()) if p.suffix == ".json" else yaml.safe_load(p.read_text())) for p in files]; print("Validated", len(files), "JSON/YAML configuration files")'

ci: test lint build-frontend
