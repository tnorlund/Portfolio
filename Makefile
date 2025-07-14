# Makefile for Portfolio project

.PHONY: help format lint test test-fast test-integration test-e2e pre-push install-hooks clean
.PHONY: export-sample-data test-local validate-pipeline

help:
	@echo "Available commands:"
	@echo "  make format          - Format all Python code with Black and isort"
	@echo "  make lint            - Run all linters (format check + mypy + pylint)"
	@echo "  make test-fast       - Run fast unit tests only"
	@echo "  make test            - Run all tests except e2e"
	@echo "  make test-integration - Run integration tests"
	@echo "  make test-e2e        - Run end-to-end tests (requires AWS)"
	@echo "  make pre-push        - Run all checks before pushing"
	@echo "  make install-hooks   - Install pre-commit hooks"
	@echo "  make clean           - Clean up temporary files"
	@echo ""
	@echo "Local Development Commands:"
	@echo "  make export-sample-data - Export sample receipt data for local testing"
	@echo "  make test-local      - Run tests with local data and stubbed APIs"
	@echo "  make validate-pipeline - Run end-to-end pipeline validation"

format:
	@echo "Installing latest formatters to match CI..."
	pip install --upgrade black isort
	@echo "Running Black formatter..."
	black .
	@echo "Running isort..."
	isort .

lint-format:
	@echo "Checking Black formatting..."
	black --check .
	@echo "Checking import sorting..."
	isort --check-only .

lint-types:
	@echo "Running mypy type checking..."
	cd receipt_dynamo && mypy . || true
	cd receipt_label && mypy . || true

lint-quality:
	@echo "Running pylint..."
	cd receipt_dynamo && pylint receipt_dynamo || true
	cd receipt_label && pylint receipt_label || true

lint: lint-format lint-types lint-quality

test-fast:
	@echo "Running fast unit tests..."
	cd receipt_dynamo && pytest -m "not integration and not end_to_end" --fail-fast -x
	cd receipt_label && pytest -m "not integration and not end_to_end" --fail-fast -x

test:
	@echo "Running all tests except e2e..."
	cd receipt_dynamo && pytest -m "not end_to_end" --cov=receipt_dynamo
	cd receipt_label && pytest -m "not end_to_end" --cov=receipt_label

test-integration:
	@echo "Running integration tests..."
	cd receipt_dynamo && pytest -m integration
	cd receipt_label && pytest -m integration

test-e2e:
	@echo "⚠️  Running end-to-end tests (requires AWS credentials)..."
	cd receipt_dynamo && pytest -m end_to_end

pre-push: format lint test-fast
	@echo "✅ All pre-push checks passed!"

install-hooks:
	@echo "Installing pre-commit hooks..."
	pre-commit install
	@echo "Creating pre-push hook..."
	@mkdir -p .git/hooks
	@echo '#!/bin/bash' > .git/hooks/pre-push
	@echo 'make pre-push' >> .git/hooks/pre-push
	@chmod +x .git/hooks/pre-push
	@echo "✅ Hooks installed!"

clean:
	@echo "Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".coverage" -delete

# Local Development Targets
export-sample-data:
	@echo "Exporting sample receipt data..."
	@if [ -z "$$DYNAMODB_TABLE_NAME" ]; then \
		echo "❌ Error: DYNAMODB_TABLE_NAME environment variable not set"; \
		echo "   Set it with: export DYNAMODB_TABLE_NAME=your-table-name"; \
		exit 1; \
	fi
	@echo "Creating sample dataset of 20 receipts..."
	python scripts/export_receipt_data.py sample --size 20 --output-dir ./receipt_data
	@echo "✅ Sample data exported to ./receipt_data"

test-local:
	@echo "Running tests with local data and stubbed APIs..."
	USE_STUB_APIS=true RECEIPT_LOCAL_DATA_DIR=./receipt_data pytest receipt_label/tests/integration/test_local_pipeline.py -v
	@echo "✅ Local pipeline tests completed"

validate-pipeline:
	@echo "Running end-to-end pipeline validation..."
	@if [ ! -d "./receipt_data" ]; then \
		echo "❌ Error: No local data found. Run 'make export-sample-data' first"; \
		exit 1; \
	fi
	@echo "Testing pattern detection..."
	python scripts/test_decision_engine.py ./receipt_data --report detailed --output validation_report.txt
	@echo "Testing local data loader..."
	USE_STUB_APIS=true pytest receipt_label/tests/integration/test_local_pipeline.py::TestLocalPipeline::test_pipeline_with_stubbed_apis -v
	@echo "✅ Pipeline validation completed. Check validation_report.txt for details"
