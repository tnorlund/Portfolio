# Makefile for Portfolio project

.PHONY: help format lint test test-fast test-integration test-e2e pre-push install-hooks clean

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

format:
	@echo "Running Black formatter..."
	black receipt_dynamo receipt_label infra
	@echo "Running isort..."
	isort receipt_dynamo receipt_label infra

lint-format:
	@echo "Checking Black formatting..."
	black --check receipt_dynamo receipt_label infra
	@echo "Checking import sorting..."
	isort --check-only receipt_dynamo receipt_label infra

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