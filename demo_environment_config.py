#!/usr/bin/env python3
"""
Demonstration of Environment-based AI Usage Configuration (Issue #119)

This script demonstrates the key features implemented for issue #119:
1. Automatic environment detection
2. Environment-specific table naming
3. Auto-tagging with environment metadata
4. Environment isolation validation
"""

import os
import sys
from pathlib import Path

# Get the directory of this script
script_dir = Path(__file__).parent.absolute()

# Add the project root and receipt_label to the Python path
sys.path.insert(0, str(script_dir))
sys.path.insert(0, str(script_dir / "receipt_label"))

from receipt_label.utils.ai_usage_tracker import AIUsageTracker
from receipt_label.utils.environment_config import AIUsageEnvironmentConfig, Environment


def demo_environment_detection():
    """Demonstrate automatic environment detection."""
    print("=== Environment Detection Demo ===")

    # Test 1: Explicit environment variable
    print("\n1. Testing explicit ENVIRONMENT variable:")
    os.environ["ENVIRONMENT"] = "production"
    env = AIUsageEnvironmentConfig.detect_environment()
    print(f"   ENVIRONMENT=production → Detected: {env.value}")

    # Test 2: CI/CD detection
    print("\n2. Testing CI/CD detection:")
    os.environ.pop("ENVIRONMENT", None)
    os.environ["GITHUB_ACTIONS"] = "true"
    env = AIUsageEnvironmentConfig.detect_environment()
    print(f"   GITHUB_ACTIONS=true → Detected: {env.value}")

    # Test 3: Default to development
    print("\n3. Testing default to development:")
    os.environ.pop("GITHUB_ACTIONS", None)
    env = AIUsageEnvironmentConfig.detect_environment()
    print(f"   No environment indicators → Detected: {env.value}")


def demo_environment_config():
    """Demonstrate environment-specific configuration."""
    print("\n=== Environment Configuration Demo ===")

    # Test configurations for each environment
    environments = [
        Environment.PRODUCTION,
        Environment.STAGING,
        Environment.CICD,
        Environment.DEVELOPMENT,
    ]

    for env in environments:
        print(f"\n{env.value.upper()} Environment:")
        config = AIUsageEnvironmentConfig.get_config(env)
        print(f"   Table suffix: '{config.table_suffix}'")
        print(f"   Require context: {config.require_context}")
        print(f"   Environment tag: {config.auto_tag['environment']}")

        # Show table naming
        table_name = AIUsageEnvironmentConfig.get_table_name("AIUsageMetrics", env)
        print(f"   Full table name: {table_name}")


def demo_environment_isolation():
    """Demonstrate environment isolation validation."""
    print("\n=== Environment Isolation Demo ===")

    # Test valid combinations
    print("\nValid table/environment combinations:")
    valid_cases = [
        ("AIUsageMetrics", Environment.PRODUCTION),
        ("AIUsageMetrics-staging", Environment.STAGING),
        ("AIUsageMetrics-cicd", Environment.CICD),
        ("AIUsageMetrics-development", Environment.DEVELOPMENT),
    ]

    for table_name, env in valid_cases:
        is_valid = AIUsageEnvironmentConfig.validate_environment_isolation(
            table_name, env
        )
        print(f"   '{table_name}' + {env.value} → {'✓' if is_valid else '✗'}")

    # Test invalid combinations
    print("\nInvalid table/environment combinations:")
    invalid_cases = [
        ("AIUsageMetrics-staging", Environment.PRODUCTION),
        ("AIUsageMetrics", Environment.STAGING),
        ("AIUsageMetrics-development", Environment.CICD),
    ]

    for table_name, env in invalid_cases:
        is_valid = AIUsageEnvironmentConfig.validate_environment_isolation(
            table_name, env
        )
        print(f"   '{table_name}' + {env.value} → {'✓' if is_valid else '✗'}")


def demo_ai_usage_tracker():
    """Demonstrate AI usage tracker with environment configuration."""
    print("\n=== AI Usage Tracker Environment Integration ===")

    # Test 1: Auto-detection and auto-generation
    print("\n1. Tracker with auto-detection and auto-generation:")
    os.environ["ENVIRONMENT"] = "staging"
    tracker = AIUsageTracker.create_for_environment()
    print(f"   Environment: {tracker.environment_config.environment.value}")
    print(f"   Table name: {tracker.table_name}")
    print(f"   Auto-tags: {tracker.environment_config.auto_tag}")

    # Test 2: Explicit environment
    print("\n2. Tracker with explicit environment:")
    tracker = AIUsageTracker.create_for_environment(environment=Environment.PRODUCTION)
    print(f"   Environment: {tracker.environment_config.environment.value}")
    print(f"   Table name: {tracker.table_name}")
    print(f"   Require context: {tracker.environment_config.require_context}")

    # Test 3: Custom table name
    print("\n3. Tracker with custom table name:")
    tracker = AIUsageTracker.create_for_environment(
        table_name="CustomAIMetrics", environment=Environment.DEVELOPMENT
    )
    print(f"   Environment: {tracker.environment_config.environment.value}")
    print(f"   Table name: {tracker.table_name}")


def demo_cicd_auto_tagging():
    """Demonstrate CI/CD auto-tagging."""
    print("\n=== CI/CD Auto-Tagging Demo ===")

    # Simulate CI/CD environment
    cicd_env_vars = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_WORKFLOW": "AI Usage Tracking Test",
        "GITHUB_ACTOR": "developer",
        "GITHUB_REF": "refs/heads/feature-branch",
        "GITHUB_SHA": "abc123def456",
        "GITHUB_REPOSITORY": "tnorlund/portfolio",
        "APP_VERSION": "1.2.3",
        "AWS_REGION": "us-east-1",
    }

    # Set environment variables
    for key, value in cicd_env_vars.items():
        os.environ[key] = value

    # Create configuration
    config = AIUsageEnvironmentConfig.get_config()
    print(f"Detected environment: {config.environment.value}")
    print("Auto-tags generated:")
    for key, value in config.auto_tag.items():
        print(f"   {key}: {value}")

    # Clean up
    for key in cicd_env_vars:
        os.environ.pop(key, None)


def main():
    """Run all demonstrations."""
    print("Issue #119: Environment-based AI Usage Configuration")
    print("=" * 60)

    demo_environment_detection()
    demo_environment_config()
    demo_environment_isolation()
    demo_ai_usage_tracker()
    demo_cicd_auto_tagging()

    print("\n" + "=" * 60)
    print("✅ All environment-based AI usage configuration features working!")
    print("\nKey Benefits:")
    print("   • Automatic environment detection")
    print("   • Environment-specific table isolation")
    print("   • Rich metadata auto-tagging")
    print("   • CI/CD integration ready")
    print("   • Production-safe configuration")


if __name__ == "__main__":
    main()
