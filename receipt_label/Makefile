# Receipt Label Test Suite Makefile
# Three-tier test architecture with production-aligned filtering

.PHONY: help test test-unit test-integration test-e2e test-fast test-all coverage clean lint format install

# Python executable
PYTHON := python3
PYTEST := $(PYTHON) -m pytest
PIP := $(PYTHON) -m pip

# Test directories
TEST_DIR := tests
UNIT_DIR := $(TEST_DIR)/unit
INTEGRATION_DIR := $(TEST_DIR)/integration
E2E_DIR := $(TEST_DIR)/end_to_end

help: ## Show this help message
	@echo "Receipt Label Test Suite"
	@echo "========================"
	@echo ""
	@echo "Available commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Test Tiers:"
	@echo "  • unit:        Fast, isolated unit tests"
	@echo "  • integration: Component interaction tests"
	@echo "  • end_to_end:  Full system workflow tests"
	@echo ""
	@echo "Environment Examples:"
	@echo "  make test-ci          # Fast CI tests"
	@echo "  make test-dev         # Comprehensive development tests"
	@echo "  make test-production  # Production validation tests"

install: ## Install package in development mode with test dependencies
	$(PIP) install -e ".[test,lambda]"

# === Primary Test Commands ===

test-fast: ## Run fast unit tests only (< 1 second each)
	@echo "🚀 Running fast unit tests..."
	$(PYTEST) -m "unit and fast and not slow" --timeout=30 -x

test-unit: ## Run all unit tests
	@echo "🔧 Running unit tests..."
	$(PYTEST) -m "unit and not unused_in_production" --timeout=60

test-integration: ## Run integration tests
	@echo "🔗 Running integration tests..."
	$(PYTEST) -m "integration and not unused_in_production" --timeout=120

test-e2e: ## Run end-to-end tests
	@echo "🎯 Running end-to-end tests..."
	$(PYTEST) -m "end_to_end and not unused_in_production" --timeout=300

test-all: ## Run all tests except deprecated features
	@echo "🧪 Running all tests..."
	$(PYTEST) -m "not unused_in_production" --timeout=300

# === Environment-Specific Test Commands ===

test-ci: ## Run tests optimized for CI environment
	@echo "⚡ Running CI-optimized tests..."
	$(PYTEST) -m "unit and fast and not slow and not aws and not openai" \
		--timeout=30 -x --tb=short --maxfail=1

test-pre-commit: ## Run tests for pre-commit hooks (fastest)
	@echo "🏃‍♂️ Running pre-commit tests..."
	$(PYTEST) -m "unit and fast and not aws and not openai and not slow" \
		--timeout=10 -x --tb=line --maxfail=1

test-dev: ## Run comprehensive development tests
	@echo "🛠️ Running development tests..."
	$(PYTEST) -m "not unused_in_production and not slow" \
		--timeout=90 --tb=long -v

test-staging: ## Run staging environment tests
	@echo "🎪 Running staging tests..."
	$(PYTEST) -m "not unused_in_production and not slow" \
		--timeout=120 --tb=short --maxfail=3

test-production: ## Run production validation tests
	@echo "🏭 Running production validation tests..."
	$(PYTEST) -m "end_to_end and pattern_detection and cost_optimization and not slow" \
		--timeout=300 --tb=short --maxfail=5

# === Feature-Specific Test Commands ===

test-patterns: ## Run pattern detection tests
	@echo "🔍 Running pattern detection tests..."
	$(PYTEST) -m "pattern_detection" --timeout=60

test-chroma: ## Run ChromaDB integration tests
	@echo "📊 Running ChromaDB tests..."
	$(PYTEST) -m "chroma" --timeout=120

test-completion: ## Run completion pipeline tests
	@echo "✅ Running completion pipeline tests..."
	$(PYTEST) -m "completion" --timeout=180

test-cost: ## Run cost optimization tests
	@echo "💰 Running cost optimization tests..."
	$(PYTEST) -m "cost_optimization" --timeout=60

# === Coverage Commands ===

coverage: ## Run tests with coverage reporting
	@echo "📈 Running tests with coverage..."
	$(PYTEST) --cov=receipt_label --cov-report=term-missing --cov-report=html \
		-m "not unused_in_production" --timeout=300

coverage-unit: ## Run unit tests with coverage
	@echo "📈 Running unit tests with coverage..."
	$(PYTEST) --cov=receipt_label --cov-report=term-missing \
		-m "unit and not unused_in_production" --timeout=120

coverage-report: ## Generate HTML coverage report
	@echo "📊 Generating coverage report..."
	$(PYTEST) --cov=receipt_label --cov-report=html \
		-m "not unused_in_production" --timeout=300
	@echo "📊 Coverage report generated in htmlcov/index.html"

# === Performance and Benchmark Commands ===

test-perf: ## Run performance and benchmark tests
	@echo "🏃 Running performance tests..."
	$(PYTEST) -m "performance" --timeout=600 -v

benchmark-patterns: ## Benchmark pattern detection performance
	@echo "⚡ Benchmarking pattern detection..."
	$(PYTEST) -m "pattern_detection and performance" --timeout=300 -v -s

# === Parallel Execution Commands ===

test-parallel: ## Run tests in parallel (auto-detect cores)
	@echo "🚄 Running tests in parallel..."
	$(PYTEST) -n auto -m "not unused_in_production and not slow" --timeout=120

test-parallel-fast: ## Run fast tests in parallel
	@echo "🚄 Running fast tests in parallel..."
	$(PYTEST) -n auto -m "unit and fast and not slow" --timeout=60

# === Debugging and Development Commands ===

test-debug: ## Run tests in debug mode (verbose, no timeout)
	@echo "🐛 Running tests in debug mode..."
	$(PYTEST) -v -s --tb=long --timeout=0

test-failed: ## Re-run only failed tests from last run
	@echo "🔄 Re-running failed tests..."
	$(PYTEST) --lf --tb=short

test-new: ## Run only new/modified tests
	@echo "🆕 Running new tests..."
	$(PYTEST) --testmon-off --cache-clear -x

# === Specific Test File Commands ===

test-file: ## Run specific test file (usage: make test-file FILE=path/to/test.py)
	@echo "📄 Running test file: $(FILE)"
	$(PYTEST) $(FILE) -v

test-pattern-unit: ## Run specific pattern detection unit tests
	@echo "🔍 Running pattern detection unit tests..."
	$(PYTEST) $(UNIT_DIR)/pattern_detection/ -v

test-chroma-integration: ## Run ChromaDB integration tests
	@echo "📊 Running ChromaDB integration tests..."
	$(PYTEST) $(INTEGRATION_DIR)/chroma_pipeline/ -v

# === Quality and Maintenance Commands ===

lint: ## Run linting checks
	@echo "🧹 Running linting..."
	$(PYTHON) -m black --check --line-length=79 receipt_label tests
	$(PYTHON) -m isort --check-only --profile=black receipt_label tests
	$(PYTHON) -m mypy receipt_label

format: ## Format code with black and isort
	@echo "✨ Formatting code..."
	$(PYTHON) -m black --line-length=79 receipt_label tests
	$(PYTHON) -m isort --profile=black receipt_label tests

clean: ## Clean up test artifacts and caches
	@echo "🧹 Cleaning up..."
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	find . -type d -name __pycache__ -delete
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".DS_Store" -delete

# === Test Status and Reporting ===

test-status: ## Show test status and statistics
	@echo "📊 Test Status Report"
	@echo "====================="
	@echo ""
	@echo "Test structure:"
	@find $(TEST_DIR) -name "test_*.py" | wc -l | xargs echo "  Test files:"
	@find $(TEST_DIR) -name "test_*.py" -exec grep -l "def test_" {} \; | wc -l | xargs echo "  Test files with tests:"
	@find $(TEST_DIR) -name "test_*.py" -exec grep -h "def test_" {} \; | wc -l | xargs echo "  Total test functions:"
	@echo ""
	@echo "Test markers:"
	@grep -r "@.*pytest.mark" $(TEST_DIR) | cut -d@ -f2 | cut -d'(' -f1 | sort | uniq -c | sort -nr
	@echo ""
	@echo "Coverage status:"
	@if [ -f .coverage ]; then \
		$(PYTHON) -m coverage report --skip-covered --precision=1; \
	else \
		echo "  No coverage data found. Run 'make coverage' first."; \
	fi

# === Environment Variables ===

# Set these via command line: make test-ci ENVIRONMENT=ci
ENVIRONMENT ?= development
USE_STUB_APIS ?= true
SKIP_PERFORMANCE_TESTS ?= false

# Export environment variables for test execution
export ENVIRONMENT
export USE_STUB_APIS 
export SKIP_PERFORMANCE_TESTS

# === Default Target ===

.DEFAULT_GOAL := help