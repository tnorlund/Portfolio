"""
Configuration for receipt_places package.

Uses pydantic-settings for environment variable management.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlacesConfig(BaseSettings):
    """Configuration for Google Places API client."""

    model_config = SettingsConfigDict(
        env_prefix="RECEIPT_PLACES_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Google Places API
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Google Places API key",
    )

    # DynamoDB Configuration
    table_name: str = Field(
        default="receipts",
        description="DynamoDB table name for caching",
    )
    aws_region: str = Field(
        default="us-west-2",
        description="AWS region for DynamoDB",
    )

    # Cache Configuration
    cache_ttl_days: int = Field(
        default=30,
        description="Cache TTL in days",
        ge=1,
        le=365,
    )
    cache_enabled: bool = Field(
        default=True,
        description="Enable/disable caching",
    )

    # API Configuration
    request_timeout: int = Field(
        default=30,
        description="HTTP request timeout in seconds",
        ge=5,
        le=120,
    )
    max_retries: int = Field(
        default=3,
        description="Maximum retry attempts for API calls",
        ge=0,
        le=10,
    )

    # Endpoint override (for testing)
    endpoint_url: str | None = Field(
        default=None,
        description="Override DynamoDB endpoint URL (for local testing)",
    )


@lru_cache
def get_config() -> PlacesConfig:
    """Get cached configuration instance."""
    return PlacesConfig()
