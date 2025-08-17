"""Production-aligned test filtering configuration."""

import os
from typing import Set


def get_ci_test_markers() -> Set[str]:
    """Get pytest markers for CI environment testing."""
    return {
        "unit and fast",  # Only fast unit tests
        "not slow",  # Exclude slow tests
        "not unused_in_production",  # Exclude deprecated features
    }


def get_pre_commit_test_markers() -> Set[str]:
    """Get pytest markers for pre-commit hook testing."""
    return {
        "unit and fast and not aws",  # Local-only, no AWS dependencies
        "not slow",
        "not unused_in_production",
    }


def get_integration_test_markers() -> Set[str]:
    """Get pytest markers for integration testing."""
    return {"integration", "not unused_in_production"}


def get_full_test_markers() -> Set[str]:
    """Get pytest markers for comprehensive testing."""
    return {"not unused_in_production"}  # Test everything except deprecated


def get_production_validation_markers() -> Set[str]:
    """Get pytest markers for production validation."""
    return {
        "end_to_end and not slow",  # Critical workflows only
        "pattern_detection and fast",  # Core cost optimization
        "chroma and not slow",  # Vector storage integrity
        "cost_optimization",  # Financial impact features
    }


def should_skip_test_in_environment(
    test_markers: Set[str], environment: str = None
) -> bool:
    """Determine if test should be skipped in current environment."""
    if environment is None:
        environment = os.environ.get("ENVIRONMENT", "development")

    environment_skip_rules = {
        "ci": {
            # Skip expensive tests in CI
            "aws and slow",
            "end_to_end and slow",
            "openai and not unit",  # No real OpenAI calls in CI
        },
        "development": {
            # Skip only deprecated features in dev
            "unused_in_production"
        },
        "staging": {
            # More comprehensive testing in staging
            "unused_in_production and slow"
        },
        "production": {
            # Only validation tests in production
            "not end_to_end",
            "slow",
            "unused_in_production",
        },
    }

    skip_patterns = environment_skip_rules.get(environment, set())

    for pattern in skip_patterns:
        if all(marker in test_markers for marker in pattern.split(" and ")):
            return True

    return False


def get_pytest_args_for_environment(environment: str = None) -> list[str]:
    """Get pytest arguments optimized for specific environment."""
    if environment is None:
        environment = os.environ.get("ENVIRONMENT", "development")

    environment_configs = {
        "ci": [
            "-m",
            "unit and fast and not slow",
            "--timeout=30",  # 30 second timeout per test
            "-x",  # Stop on first failure
            "--tb=short",  # Concise tracebacks
        ],
        "pre_commit": [
            "-m",
            "unit and fast and not aws and not openai",
            "--timeout=10",  # Even faster for pre-commit
            "-x",
            "--tb=line",  # Minimal tracebacks
        ],
        "development": [
            "-m",
            "not unused_in_production",
            "--timeout=60",  # More time for dev debugging
            "--tb=long",  # Detailed tracebacks
            "-v",  # Verbose output
        ],
        "integration": [
            "-m",
            "integration and not unused_in_production",
            "--timeout=120",  # Integration tests can be slower
            "--tb=short",
        ],
        "staging": [
            "-m",
            "not unused_in_production and not slow",
            "--timeout=90",
            "--tb=short",
        ],
        "production_validation": [
            "-m",
            "end_to_end and pattern_detection and cost_optimization",
            "--timeout=300",  # Production validation can take time
            "--tb=short",
            "--maxfail=5",  # Don't overwhelm with failures
        ],
    }

    return environment_configs.get(
        environment, environment_configs["development"]
    )


def get_coverage_config_for_environment(environment: str = None) -> dict:
    """Get coverage configuration for specific environment."""
    if environment is None:
        environment = os.environ.get("ENVIRONMENT", "development")

    base_config = {
        "source": ["receipt_label"],
        "omit": ["*/tests/*", "*/test_*", "*/__pycache__/*"],
    }

    environment_configs = {
        "ci": {
            **base_config,
            "fail_under": 21,  # Current baseline
            "show_missing": True,
            "skip_covered": True,
        },
        "development": {
            **base_config,
            "show_missing": True,
            "report": ["term-missing", "html"],
        },
        "integration": {
            **base_config,
            "include": [
                "receipt_label/pattern_detection/*",
                "receipt_label/completion/*",
                "receipt_label/utils/chroma_*",
            ],
            "show_missing": True,
        },
        "production_validation": {
            **base_config,
            "include": [
                "receipt_label/pattern_detection/*",
                "receipt_label/utils/chroma_*",
                "receipt_label/utils/cost_*",
            ],
            "fail_under": 50,  # Higher bar for critical code
            "show_missing": True,
        },
    }

    return environment_configs.get(
        environment, environment_configs["development"]
    )


class ExecutionStrategy:
    """Strategy pattern for test execution in different environments."""

    def __init__(self, environment: str = None):
        self.environment = environment or os.environ.get(
            "ENVIRONMENT", "development"
        )

    def should_run_test(self, test_markers: Set[str]) -> bool:
        """Determine if test should run in current environment."""
        return not should_skip_test_in_environment(
            test_markers, self.environment
        )

    def get_pytest_args(self) -> list[str]:
        """Get pytest arguments for current environment."""
        return get_pytest_args_for_environment(self.environment)

    def get_coverage_config(self) -> dict:
        """Get coverage configuration for current environment."""
        return get_coverage_config_for_environment(self.environment)

    def estimate_execution_time(self, test_count: int) -> int:
        """Estimate test execution time in seconds."""
        time_per_test = {
            "ci": 2,  # Fast tests only
            "pre_commit": 1,  # Fastest possible
            "development": 5,  # More comprehensive
            "integration": 15,  # Complex workflows
            "staging": 10,  # Balanced approach
            "production_validation": 30,  # Thorough validation
        }

        return test_count * time_per_test.get(self.environment, 5)

    def get_parallel_workers(self) -> int:
        """Get optimal number of parallel workers."""
        worker_config = {
            "ci": 2,  # Conservative for CI resources
            "pre_commit": 1,  # Sequential for speed
            "development": 4,  # Utilize dev machine
            "integration": 2,  # Avoid resource contention
            "staging": 3,  # Balanced parallelism
            "production_validation": 1,  # Sequential for reliability
        }

        return worker_config.get(self.environment, 2)


# Convenience functions for common test scenarios


def get_fast_feedback_args() -> list[str]:
    """Get pytest args for fastest possible feedback."""
    return get_pytest_args_for_environment("pre_commit")


def get_comprehensive_test_args() -> list[str]:
    """Get pytest args for comprehensive testing."""
    return get_pytest_args_for_environment("development")


def get_production_ready_args() -> list[str]:
    """Get pytest args for production readiness validation."""
    return get_pytest_args_for_environment("production_validation")
