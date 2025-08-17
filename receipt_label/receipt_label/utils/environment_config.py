"""
Environment-based configuration for AI usage tracking.

This module provides automatic environment detection and configuration
for AI usage tracking, ensuring proper isolation between environments.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class Environment(Enum):
    """Supported environments for AI usage tracking."""

    PRODUCTION = "production"
    STAGING = "staging"
    CICD = "cicd"
    DEVELOPMENT = "development"


@dataclass
class EnvironmentConfig:
    """Configuration for AI usage tracking based on environment."""

    environment: Environment
    table_suffix: str
    require_context: bool
    auto_tag: Dict[str, Any]

    @property
    def table_name_suffix(self) -> str:
        """Get the table name suffix for this environment."""
        return self.table_suffix

    @property
    def is_production(self) -> bool:
        """Check if this is production environment."""
        return self.environment == Environment.PRODUCTION

    @property
    def is_cicd(self) -> bool:
        """Check if this is CI/CD environment."""
        return self.environment == Environment.CICD


class AIUsageEnvironmentConfig:
    """Manages environment-based configuration for AI usage tracking."""

    @staticmethod
    def detect_environment() -> Environment:
        """
        Automatically detect the current environment based on environment variables.

        Returns:
            Environment: The detected environment
        """
        # Check explicit environment setting
        env_var = os.getenv("ENVIRONMENT", "").lower()
        if env_var:
            try:
                return Environment(env_var)
            except ValueError:
                pass

        # Check Pulumi stack name - only care about production
        stack_name = os.getenv(
            "PULUMI_STACK", os.getenv("PULUMI_STACK_NAME", "")
        ).lower()
        if stack_name and "prod" in stack_name:
            return Environment.PRODUCTION

        # Detect CI/CD environment
        if any(
            os.getenv(var)
            for var in [
                "GITHUB_ACTIONS",
                "CI",
                "CONTINUOUS_INTEGRATION",
                "GITLAB_CI",
                "JENKINS_URL",
            ]
        ):
            return Environment.CICD

        # Default to development
        return Environment.DEVELOPMENT

    @classmethod
    def get_config(
        cls, environment: Optional[Environment] = None
    ) -> EnvironmentConfig:
        """
        Get configuration for the specified or detected environment.

        Args:
            environment: Optional specific environment to get config for.
                        If None, will auto-detect.

        Returns:
            EnvironmentConfig: Configuration for the environment
        """
        if environment is None:
            environment = cls.detect_environment()

        # Base configuration
        config = {
            "environment": environment,
            "table_suffix": cls._get_table_suffix(environment),
            "require_context": environment == Environment.PRODUCTION,
            "auto_tag": cls._get_auto_tags(environment),
        }

        return EnvironmentConfig(
            environment=config["environment"],
            table_suffix=config["table_suffix"],
            require_context=config["require_context"],
            auto_tag=config["auto_tag"],
        )

    @staticmethod
    def _get_table_suffix(environment: Environment) -> str:
        """Get table suffix for the environment."""
        if environment == Environment.PRODUCTION:
            return ""  # No suffix for production
        else:
            return f"-{environment.value}"

    @staticmethod
    def _get_auto_tags(environment: Environment) -> Dict[str, Any]:
        """Get automatic tags for the environment."""
        tags = {
            "environment": environment.value,
            "service": "receipt-processing",
            "version": os.getenv("APP_VERSION", "unknown"),
            "region": os.getenv(
                "AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "unknown")
            ),
        }

        # Add environment-specific tags
        if environment == Environment.CICD:
            tags.update(
                {
                    "ci_run_id": os.getenv("GITHUB_RUN_ID"),
                    "ci_workflow": os.getenv("GITHUB_WORKFLOW"),
                    "ci_actor": os.getenv("GITHUB_ACTOR"),
                    "ci_ref": os.getenv("GITHUB_REF"),
                    "ci_sha": os.getenv("GITHUB_SHA"),
                    "ci_repository": os.getenv("GITHUB_REPOSITORY"),
                }
            )
        elif environment == Environment.PRODUCTION:
            tags.update(
                {
                    "deployment_id": os.getenv("DEPLOYMENT_ID"),
                    "instance_id": os.getenv(
                        "AWS_LAMBDA_FUNCTION_NAME",
                        os.getenv("EC2_INSTANCE_ID"),
                    ),
                }
            )
        elif environment == Environment.STAGING:
            tags.update(
                {
                    "staging_branch": os.getenv("BRANCH_NAME", "main"),
                    "staging_deployment": os.getenv("STAGING_DEPLOYMENT_ID"),
                }
            )
        else:  # DEVELOPMENT
            tags.update(
                {
                    "developer": os.getenv(
                        "USER", os.getenv("USERNAME", "unknown")
                    ),
                    "machine": os.getenv("HOSTNAME", "unknown"),
                    "local_branch": os.getenv("GIT_BRANCH"),
                }
            )

        # Remove None values
        return {k: v for k, v in tags.items() if v is not None}

    @staticmethod
    def get_table_name(
        base_table_name: str, environment: Optional[Environment] = None
    ) -> str:
        """
        Get the full table name with environment suffix.

        Args:
            base_table_name: Base table name (e.g., "AIUsageMetrics")
            environment: Optional environment. If None, will auto-detect.

        Returns:
            str: Full table name with environment suffix
        """
        if environment is None:
            environment = AIUsageEnvironmentConfig.detect_environment()

        suffix = AIUsageEnvironmentConfig._get_table_suffix(environment)
        return f"{base_table_name}{suffix}"

    @staticmethod
    def validate_environment_isolation(
        table_name: str, expected_environment: Environment
    ) -> bool:
        """
        Validate that table name matches expected environment isolation.

        Args:
            table_name: The table name to validate
            expected_environment: Expected environment

        Returns:
            bool: True if table name is valid for environment
        """
        # Only validate in production - development can use any table name
        if expected_environment != Environment.PRODUCTION:
            return True

        # Production should not have development/staging suffixes
        return not any(
            table_name.endswith(f"-{env.value}")
            for env in Environment
            if env != Environment.PRODUCTION
        )
