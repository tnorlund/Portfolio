"""
Tests for environment-based configuration system.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from receipt_label.utils.environment_config import (
    AIUsageEnvironmentConfig,
    Environment,
    EnvironmentConfig,
)


class TestEnvironmentDetection:
    """Test automatic environment detection."""

    def test_detect_explicit_environment_variable(self):
        """Test detection when ENVIRONMENT is explicitly set."""
        with patch.dict(
            os.environ, {"ENVIRONMENT": "production"}, clear=False
        ):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.PRODUCTION

        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.STAGING

    def test_detect_cicd_environment(self):
        """Test detection of CI/CD environment."""
        # GitHub Actions
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=False):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.CICD

        # Generic CI
        with patch.dict(os.environ, {"CI": "true"}, clear=False):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.CICD

        # GitLab CI
        with patch.dict(os.environ, {"GITLAB_CI": "true"}, clear=False):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.CICD

    def test_detect_production_by_aws_context(self):
        """Test detection of production based on AWS context."""
        with patch.dict(
            os.environ,
            {
                "AWS_EXECUTION_ENV": "AWS_Lambda_python3.12",
                "PULUMI_STACK_NAME": "tnorlund/portfolio/prod",
            },
            clear=False,
        ):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.PRODUCTION

    def test_detect_staging_by_aws_context(self):
        """Test detection of staging based on AWS context."""
        with patch.dict(
            os.environ,
            {
                "AWS_EXECUTION_ENV": "AWS_Lambda_python3.12",
                "PULUMI_STACK_NAME": "tnorlund/portfolio/staging",
            },
            clear=False,
        ):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.STAGING

    def test_detect_development_default(self):
        """Test that development is the default environment."""
        # Clear relevant environment variables
        env_vars_to_clear = [
            "ENVIRONMENT",
            "GITHUB_ACTIONS",
            "CI",
            "GITLAB_CI",
            "AWS_EXECUTION_ENV",
            "PULUMI_STACK_NAME",
        ]

        with patch.dict(os.environ, {}, clear=True):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.DEVELOPMENT

    def test_detect_invalid_environment_falls_back_to_default(self):
        """Test that invalid environment variables fall back to detection logic."""
        with patch.dict(os.environ, {"ENVIRONMENT": "invalid"}, clear=False):
            env = AIUsageEnvironmentConfig.detect_environment()
            assert env == Environment.DEVELOPMENT


class TestEnvironmentConfig:
    """Test environment configuration generation."""

    def test_production_config(self):
        """Test production environment configuration."""
        config = AIUsageEnvironmentConfig.get_config(Environment.PRODUCTION)

        assert config.environment == Environment.PRODUCTION
        assert config.table_suffix == ""
        assert config.require_context is True
        assert config.is_production is True
        assert config.is_cicd is False

    def test_staging_config(self):
        """Test staging environment configuration."""
        config = AIUsageEnvironmentConfig.get_config(Environment.STAGING)

        assert config.environment == Environment.STAGING
        assert config.table_suffix == "-staging"
        assert config.require_context is False
        assert config.is_production is False

    def test_cicd_config(self):
        """Test CI/CD environment configuration."""
        with patch.dict(
            os.environ,
            {
                "GITHUB_RUN_ID": "12345",
                "GITHUB_WORKFLOW": "test",
                "GITHUB_ACTOR": "user",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_SHA": "abc123",
                "GITHUB_REPOSITORY": "user/repo",
            },
            clear=False,
        ):
            config = AIUsageEnvironmentConfig.get_config(Environment.CICD)

            assert config.environment == Environment.CICD
            assert config.table_suffix == "-cicd"
            assert config.require_context is False
            assert config.is_cicd is True

            # Check CI-specific auto-tags
            assert config.auto_tag["ci_run_id"] == "12345"
            assert config.auto_tag["ci_workflow"] == "test"
            assert config.auto_tag["ci_actor"] == "user"

    def test_development_config(self):
        """Test development environment configuration."""
        with patch.dict(
            os.environ,
            {
                "USER": "developer",
                "HOSTNAME": "dev-machine",
                "GIT_BRANCH": "feature-branch",
            },
            clear=False,
        ):
            config = AIUsageEnvironmentConfig.get_config(
                Environment.DEVELOPMENT
            )

            assert config.environment == Environment.DEVELOPMENT
            assert config.table_suffix == "-development"
            assert config.require_context is False

            # Check development-specific auto-tags
            assert config.auto_tag["developer"] == "developer"
            assert config.auto_tag["machine"] == "dev-machine"
            assert config.auto_tag["local_branch"] == "feature-branch"

    def test_auto_tags_common_fields(self):
        """Test that all environments include common auto-tags."""
        with patch.dict(
            os.environ,
            {
                "APP_VERSION": "1.2.3",
                "AWS_REGION": "us-east-1",
            },
            clear=False,
        ):
            config = AIUsageEnvironmentConfig.get_config(
                Environment.PRODUCTION
            )

            assert config.auto_tag["environment"] == "production"
            assert config.auto_tag["service"] == "receipt-processing"
            assert config.auto_tag["version"] == "1.2.3"
            assert config.auto_tag["region"] == "us-east-1"

    def test_auto_tags_remove_none_values(self):
        """Test that None values are removed from auto-tags."""
        # Ensure environment variables are not set
        with patch.dict(os.environ, {}, clear=True):
            config = AIUsageEnvironmentConfig.get_config(Environment.CICD)

            # CI-specific tags should not be present if environment variables are None
            assert "ci_run_id" not in config.auto_tag
            assert "ci_workflow" not in config.auto_tag
            assert "ci_actor" not in config.auto_tag


class TestTableNaming:
    """Test environment-based table naming."""

    def test_production_table_name(self):
        """Test that production tables have no suffix."""
        table_name = AIUsageEnvironmentConfig.get_table_name(
            "AIUsageMetrics", Environment.PRODUCTION
        )
        assert table_name == "AIUsageMetrics"

    def test_staging_table_name(self):
        """Test that staging tables have -staging suffix."""
        table_name = AIUsageEnvironmentConfig.get_table_name(
            "AIUsageMetrics", Environment.STAGING
        )
        assert table_name == "AIUsageMetrics-staging"

    def test_cicd_table_name(self):
        """Test that CI/CD tables have -cicd suffix."""
        table_name = AIUsageEnvironmentConfig.get_table_name(
            "AIUsageMetrics", Environment.CICD
        )
        assert table_name == "AIUsageMetrics-cicd"

    def test_development_table_name(self):
        """Test that development tables have -development suffix."""
        table_name = AIUsageEnvironmentConfig.get_table_name(
            "AIUsageMetrics", Environment.DEVELOPMENT
        )
        assert table_name == "AIUsageMetrics-development"

    def test_auto_detect_table_name(self):
        """Test table naming with auto-detected environment."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=False):
            table_name = AIUsageEnvironmentConfig.get_table_name(
                "AIUsageMetrics"
            )
            assert table_name == "AIUsageMetrics-staging"


class TestEnvironmentIsolation:
    """Test environment isolation validation."""

    def test_production_isolation_valid(self):
        """Test that production table names are correctly validated."""
        # Valid production table names (no suffix)
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics", Environment.PRODUCTION
        )
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "MyTable", Environment.PRODUCTION
        )

    def test_production_isolation_invalid(self):
        """Test that non-production suffixes are rejected for production."""
        # Invalid production table names (have suffixes)
        assert not AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-staging", Environment.PRODUCTION
        )
        assert not AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-development", Environment.PRODUCTION
        )
        assert not AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-cicd", Environment.PRODUCTION
        )

    def test_staging_isolation_valid(self):
        """Test that staging table names are correctly validated."""
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-staging", Environment.STAGING
        )
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "MyTable-staging", Environment.STAGING
        )

    def test_staging_isolation_invalid(self):
        """Test that incorrect suffixes are rejected for staging."""
        assert not AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics", Environment.STAGING
        )
        assert not AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-development", Environment.STAGING
        )

    def test_cicd_isolation_valid(self):
        """Test that CI/CD table names are correctly validated."""
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-cicd", Environment.CICD
        )
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "MyTable-cicd", Environment.CICD
        )

    def test_development_isolation_valid(self):
        """Test that development table names are correctly validated."""
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "AIUsageMetrics-development", Environment.DEVELOPMENT
        )
        assert AIUsageEnvironmentConfig.validate_environment_isolation(
            "MyTable-development", Environment.DEVELOPMENT
        )


class TestEnvironmentConfigDataclass:
    """Test the EnvironmentConfig dataclass."""

    def test_environment_config_properties(self):
        """Test EnvironmentConfig properties."""
        config = EnvironmentConfig(
            environment=Environment.PRODUCTION,
            table_suffix="",
            require_context=True,
            auto_tag={"env": "prod"},
        )

        assert config.table_name_suffix == ""
        assert config.is_production is True
        assert config.is_cicd is False

        config_staging = EnvironmentConfig(
            environment=Environment.STAGING,
            table_suffix="-staging",
            require_context=False,
            auto_tag={"env": "staging"},
        )

        assert config_staging.table_name_suffix == "-staging"
        assert config_staging.is_production is False
        assert config_staging.is_cicd is False

        config_cicd = EnvironmentConfig(
            environment=Environment.CICD,
            table_suffix="-cicd",
            require_context=False,
            auto_tag={"env": "cicd"},
        )

        assert config_cicd.table_name_suffix == "-cicd"
        assert config_cicd.is_production is False
        assert config_cicd.is_cicd is True
