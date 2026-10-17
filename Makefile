# Shortcuts for the commands used in every phase. Run `make help` to list them.
#
# Example:
#   make install   # create .venv and install pinned deps
#   make test      # run the full test suite with coverage
#   make run       # start the API on http://localhost:8000

PY := .venv/bin/python
PORT ?= 8000

.PHONY: help install test lint format run test-alerts lock render security helm-check

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

PROMETHEUS_IMAGE := prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e

test-alerts:  ## Unit-test Prometheus alert rules with promtool (needs Docker)
	mkdir -p .build/alerts
	$(PY) scripts/extract_rules.py monitoring/alerts/items-api-rules.yaml > .build/alerts/items-api.rules.yaml
	cp monitoring/alerts/items-api-rules.test.yaml .build/alerts/
	docker run --rm -v "$(CURDIR)/.build/alerts:/work:ro" -w /work --entrypoint promtool $(PROMETHEUS_IMAGE) check rules items-api.rules.yaml
	docker run --rm -v "$(CURDIR)/.build/alerts:/work:ro" -w /work --entrypoint promtool $(PROMETHEUS_IMAGE) test rules items-api-rules.test.yaml

IMAGE ?= items-api:ci

render:  ## Render every Kustomize tree to .build/rendered (what kubectl apply really sends)
	rm -rf .build/rendered && mkdir -p .build/rendered
	kubectl kustomize k8s/overlays/local > .build/rendered/app.yaml
	kubectl kustomize monitoring > .build/rendered/monitoring.yaml
	kubectl kustomize load-tests > .build/rendered/load-tests.yaml

security: render  ## Dependency, secret, misconfiguration and image scans (what CI runs)
	@echo "== 1. Python dependencies (pip-audit, exact locked versions)"
	$(PY) -m pip_audit --progress-spinner off --require-hashes --disable-pip -r requirements.lock
	@echo "== 2. Secrets committed to the repo (trivy fs)"
	trivy fs --quiet --scanners secret --exit-code 1 --skip-dirs .venv,.build,load-tests/results .
	@echo "== 3. Misconfiguration in Dockerfile and RENDERED manifests (trivy config)"
	trivy config --quiet --exit-code 1 --severity MEDIUM,HIGH,CRITICAL --ignorefile .trivyignore.yaml Dockerfile
	trivy config --quiet --exit-code 1 --severity MEDIUM,HIGH,CRITICAL --ignorefile .trivyignore.yaml .build/rendered
	@echo "== 4. Image vulnerabilities with a fix available (trivy image)"
	docker build -q -t $(IMAGE) .
	trivy image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 $(IMAGE)

helm-check:  ## Lint the Helm chart and prove it renders the same workload as Kustomize
	helm lint helm/items-api --strict
	mkdir -p .build
	kubectl kustomize k8s/overlays/local > .build/kustomize.yaml
	helm template items-api helm/items-api -n autoscale-app > .build/helm.yaml
	$(PY) scripts/compare_helm_kustomize.py .build/kustomize.yaml .build/helm.yaml
