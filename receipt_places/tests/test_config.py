"""
Tests for PlacesConfig.
"""

import os
from unittest.mock import patch

import pytest

from receipt_places.config import PlacesConfig, get_config


class TestPlacesConfig:
    """Test configuration handling."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        config = PlacesConfig()

        assert config.table_name == "receipts"
        assert config.aws_region == "us-west-2"
        assert config.cache_ttl_days == 30
        assert config.cache_enabled is True
        assert config.request_timeout == 30
        assert config.max_retries == 3
        assert config.endpoint_url is None

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        config = PlacesConfig(
            api_key="custom-key",
            table_name="custom-table",
            aws_region="us-east-1",
            cache_ttl_days=7,
            cache_enabled=False,
            request_timeout=60,
        )

        assert config.api_key.get_secret_value() == "custom-key"
        assert config.table_name == "custom-table"
        assert config.aws_region == "us-east-1"
        assert config.cache_ttl_days == 7
        assert config.cache_enabled is False
        assert config.request_timeout == 60

    def test_env_variables(self) -> None:
        """Test configuration from environment variables."""
        with patch.dict(
            os.environ,
            {
                "RECEIPT_PLACES_API_KEY": "env-key",
                "RECEIPT_PLACES_TABLE_NAME": "env-table",
                "RECEIPT_PLACES_CACHE_TTL_DAYS": "14",
            },
        ):
            # Clear cache
            get_config.cache_clear()
            config = PlacesConfig()

            assert config.api_key.get_secret_value() == "env-key"
            assert config.table_name == "env-table"
            assert config.cache_ttl_days == 14

    def test_cache_ttl_validation(self) -> None:
        """Test cache TTL validation."""
        # Too low
        with pytest.raises(ValueError):
            PlacesConfig(cache_ttl_days=0)

        # Too high
        with pytest.raises(ValueError):
            PlacesConfig(cache_ttl_days=400)

    def test_request_timeout_validation(self) -> None:
        """Test request timeout validation."""
        # Too low
        with pytest.raises(ValueError):
            PlacesConfig(request_timeout=2)

        # Too high
        with pytest.raises(ValueError):
            PlacesConfig(request_timeout=200)

    def test_get_config_cached(self) -> None:
        """Test get_config returns cached instance."""
        get_config.cache_clear()

        config1 = get_config()
        config2 = get_config()

        assert config1 is config2
