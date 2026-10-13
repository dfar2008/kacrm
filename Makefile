# §69 — Make targets for the RL loop work.
#
# Why Make (not GitHub Actions):
#   This repo's origin is Gitee; .github/workflows/ wouldn't fire. A
#   Makefile is the portable middle layer — works locally, works in
#   Gitee Go jobs (one-liner `make ci`), works in whatever CI the
#   team later migrates to.
#
# Philosophy: each target should be a single command that already works
# in one place. If a target grows multi-step shell logic, extract it to
# scripts/ and have the target shell out.

PYTHON ?= .venv/bin/python
PYTEST ?= $(PYTHON) -m pytest

# test_dev_server imports psycopg2 at module scope; pre-existing, see
# docs/rl_loop_e2e.md §64. Skip it globally until that's fixed.
PYTEST_IGNORE := --ignore=tests/hermes_cloud/test_dev_server.py

.PHONY: help install test test-rl test-llm-eval smoke rl-pause-check lint lint-sh lint-yml lint-json lint-frontend ci

help:
	@echo "Hermes Cloud — RL loop targets"
	@echo ""
	@echo "  make install       install runtime + dev deps into ./.venv"
	@echo "  make test          full pytest (minus pre-existing broken collectors)"
	@echo "  make test-rl       only RL-related tests (faster inner loop)"
	@echo "  make smoke         build_v13_bundle.sh subprocess smoke test"
	@echo "  make rl-pause-check validate RL no-train pause guardrails end-to-end"
	@echo "  make lint          lint-sh + lint-yml + lint-json"
	@echo "  make lint-sh       shellcheck scripts/*.sh (skip if not installed)"
	@echo "  make lint-yml      yaml.safe_load scripts/*.yml"
	@echo "  make lint-json     json.load scripts/*.json"
	@echo "  make ci            test + lint — what a CI job should run"
	@echo ""
	@echo "  PYTHON=... override interpreter (default .venv/bin/python)"

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTEST) $(PYTEST_IGNORE) -m "not llm_eval" tests/

# Specialist prompt eval — calls real LLM. Per Charter, the model
# comes from the profile/user config (mirrors production): set
# EVAL_USER_ID (+ optional EVAL_PROFILE_ID) to resolve from
# user_models / profile_models. For ad-hoc cross-model checks, set
# explicit EVAL_BASE_URL + EVAL_MODEL + EVAL_API_KEY instead.
# Skips automatically if no model can be resolved. CI gates on
# recall ≥ 85% / precision ≥ 80% across whatever model is configured;
# see docs/2026-04-26-specialist-prompt-eval.md.
test-llm-eval:
	$(PYTEST) -m llm_eval tests/agents/specialist_eval/ -v -s

# Heuristic: RL tests live either under tests/hermes_cloud/ matching
# rl/signal/adapter/judge/dlq patterns, or name the module explicitly.
# Keep the list small enough that `make test-rl` stays under ~10s on
# dev laptops — otherwise devs stop running it.
# Explicit file list so we don't grep in unrelated test modules whose
# names happen to contain "rl" / "signal" (e.g. wechat has "signal"-ish
# imports that break under -k matching).
RL_TESTS := \
  tests/hermes_cloud/test_adapter_store.py \
  tests/hermes_cloud/test_async_capture_artifacts.py \
  tests/hermes_cloud/test_build_v13_bundle_script.py \
  tests/hermes_cloud/test_enable_async_capture_script.py \
  tests/hermes_cloud/test_rl_canary.py \
  tests/hermes_cloud/test_rl_cli.py \
  tests/hermes_cloud/test_rl_dlq_drain_metrics.py \
  tests/hermes_cloud/test_rl_dlq_metrics.py \
  tests/hermes_cloud/test_rl_pipeline_smoke.py \
  tests/hermes_cloud/test_rl_post_training_smoke.py \
  tests/hermes_cloud/test_rl_prom_metrics.py \
  tests/hermes_cloud/test_probe_adapter_script.py \
  tests/hermes_cloud/test_signal_export.py

test-rl:
	$(PYTEST) $(PYTEST_IGNORE) $(RL_TESTS)

smoke:
	$(PYTEST) tests/hermes_cloud/test_build_v13_bundle_script.py -v

rl-pause-check:
	PYTHON_BIN=$(PYTHON) ./scripts/check_rl_pause_mode.sh

lint: lint-sh lint-yml lint-json lint-frontend

lint-sh:
	@if command -v shellcheck >/dev/null 2>&1; then \
	  echo "shellcheck scripts/*.sh"; \
	  shellcheck scripts/*.sh; \
	else \
	  echo "shellcheck not installed — skipping (install: brew install shellcheck)"; \
	fi

lint-yml:
	@for f in scripts/*.yml scripts/*.yaml .workflow/*.yml .workflow/*.yaml; do \
	  [ -f "$$f" ] || continue; \
	  echo "yaml: $$f"; \
	  $(PYTHON) -c "import sys,yaml; yaml.safe_load(open(sys.argv[1]))" "$$f" || exit 1; \
	done

lint-json:
	@for f in scripts/*.json; do \
	  [ -f "$$f" ] || continue; \
	  echo "json: $$f"; \
	  $(PYTHON) -c "import sys,json; json.load(open(sys.argv[1]))" "$$f" || exit 1; \
	done

# ESLint anti-corruption rule for /app/decisions surface
# (spec docs/superpowers/specs/2026-05-10-decisions-page-design.md §四 + §7.1).
# Skip silently if node_modules isn't installed — local dev that hasn't
# touched the SPA shouldn't be blocked by missing JS deps.
lint-frontend:
	@if [ -d frontend/node_modules ]; then \
	  echo "eslint frontend (decisions anti-corruption)"; \
	  cd frontend && npm run lint --silent; \
	else \
	  echo "frontend/node_modules absent — skipping (run: cd frontend && npm install)"; \
	fi

ci: test lint rl-pause-check
