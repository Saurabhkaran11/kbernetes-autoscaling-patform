# Shortcuts for the commands used in every phase. Run `make help` to list them.
#
# Example:
#   make install   # create .venv and install pinned deps
#   make test      # run the full test suite with coverage
#   make run       # start the API on http://localhost:8000

PY := .venv/bin/python
PORT ?= 8000

.PHONY: help install test lint format run test-alerts lock

help:  ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create .venv (Python 3.12) and install runtime + dev deps
	python3.12 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

test:  ## Run all tests with a coverage report
	$(PY) -m pytest -v --cov=app --cov-report=term-missing

lint:  ## Lint and check formatting (what CI runs)
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:  ## Auto-fix lint issues and format code
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

lock:  ## Re-pin every runtime dep (direct + indirect) with sha256 hashes after editing requirements.txt
	uv pip compile requirements.txt --python-version 3.12 --python-platform linux \
		--generate-hashes --custom-compile-command "make lock" -o requirements.lock

run:  ## Start the API with auto-reload
	$(PY) -m uvicorn app.main:app --reload --port $(PORT) --no-access-log

PROMETHEUS_IMAGE := prom/prometheus:v3.15.0

test-alerts:  ## Unit-test Prometheus alert rules with promtool (needs Docker)
	mkdir -p .build/alerts
	$(PY) scripts/extract_rules.py monitoring/alerts/items-api-rules.yaml > .build/alerts/items-api.rules.yaml
	cp monitoring/alerts/items-api-rules.test.yaml .build/alerts/
	docker run --rm -v "$(CURDIR)/.build/alerts:/work:ro" -w /work --entrypoint promtool $(PROMETHEUS_IMAGE) check rules items-api.rules.yaml
	docker run --rm -v "$(CURDIR)/.build/alerts:/work:ro" -w /work --entrypoint promtool $(PROMETHEUS_IMAGE) test rules items-api-rules.test.yaml
