# Developer entry points. Every target here is what CI runs, so a green local
# run means a green pipeline.

.DEFAULT_GOAL := help
PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: venv
venv: ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

.PHONY: install
install: venv ## Install the package with development dependencies
	$(BIN)/pip install -e ".[dev]"
	$(BIN)/pre-commit install

.PHONY: format
format: ## Format the code
	$(BIN)/ruff format src tests
	$(BIN)/ruff check src tests --fix

.PHONY: lint
lint: ## Lint without modifying anything
	$(BIN)/ruff check src tests
	$(BIN)/ruff format --check src tests

.PHONY: typecheck
typecheck: ## Run the strict type checker
	$(BIN)/mypy

.PHONY: test
test: ## Run the test suite with coverage
	$(BIN)/pytest

.PHONY: test-fast
test-fast: ## Run the unit tests only
	$(BIN)/pytest tests/unit -q --no-cov

.PHONY: audit
audit: ## Check dependencies for known vulnerabilities
	$(BIN)/pip-audit --strict --desc

.PHONY: check
check: lint typecheck test ## Everything CI runs

.PHONY: schema
schema: ## Regenerate the published specification JSON Schema
	$(BIN)/insight-engine schema -o schemas/analysis-spec-v1.json

.PHONY: run
run: ## Start the development server with reload
	$(BIN)/insight-engine serve --reload --log-format text

.PHONY: demo
demo: ## Generate a report from the bundled example
	$(BIN)/insight-engine run examples/configs/marketing-csv.yaml --log-format text

.PHONY: docker
docker: ## Build the container image
	docker build -f deploy/Dockerfile -t insight-engine:local \
		--build-arg VERSION=$$($(BIN)/python -c "import insight_engine;print(insight_engine.__version__)") \
		--build-arg VCS_REF=$$(git rev-parse --short HEAD) \
		--build-arg BUILD_DATE=$$(date -u +%Y-%m-%dT%H:%M:%SZ) .

.PHONY: clean
clean: ## Remove caches and build output
	rm -rf build dist *.egg-info .mypy_cache .ruff_cache .pytest_cache htmlcov \
		.coverage coverage.xml var tmp
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
