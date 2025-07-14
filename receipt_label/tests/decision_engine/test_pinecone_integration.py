"""Unit tests for Pinecone integration in Smart Decision Engine."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from receipt_label.decision_engine import (
    DecisionEngineConfig,
    MerchantReliabilityData,
    PineconeDecisionHelper,
)


class TestPineconeDecisionHelper:
    """Test cases for PineconeDecisionHelper."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_pinecone_client = Mock()
        self.config = DecisionEngineConfig(
            enable_pinecone_validation=True, max_pinecone_queries=2
        )
        self.helper = PineconeDecisionHelper(
            self.mock_pinecone_client, self.config
        )

    @pytest.mark.asyncio
    async def test_pinecone_disabled(self):
        """Test behavior when Pinecone validation is disabled."""
        config = DecisionEngineConfig(enable_pinecone_validation=False)
        helper = PineconeDecisionHelper(self.mock_pinecone_client, config)

        result = await helper.get_merchant_reliability("Walmart")

        assert result is None
        self.mock_pinecone_client.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_merchant_name(self):
        """Test handling of empty merchant name."""
        result = await self.helper.get_merchant_reliability("")
        assert result is None

        result = await self.helper.get_merchant_reliability(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_merchant_lookup(self):
        """Test successful merchant reliability lookup."""
        # Mock Pinecone response
        mock_response = Mock()
        mock_response.matches = [
            Mock(
                metadata={
                    "receipt_id": "receipt_1",
                    "merchant_name": "WALMART",
                    "valid_labels": ["MERCHANT_NAME", "GRAND_TOTAL", "DATE"],
                    "invalid_labels": [],
                    "timestamp": datetime.now().isoformat(),
                }
            ),
            Mock(
                metadata={
                    "receipt_id": "receipt_2",
                    "merchant_name": "WALMART",
                    "valid_labels": [
                        "MERCHANT_NAME",
                        "GRAND_TOTAL",
                        "PRODUCT_NAME",
                    ],
                    "invalid_labels": ["ADDRESS"],
                    "timestamp": datetime.now().isoformat(),
                }
            ),
            Mock(
                metadata={
                    "receipt_id": "receipt_3",
                    "merchant_name": "WALMART",
                    "valid_labels": ["MERCHANT_NAME", "DATE"],
                    "invalid_labels": ["PHONE_NUMBER"],
                    "timestamp": datetime.now().isoformat(),
                }
            ),
        ]

        self.mock_pinecone_client.query.return_value = mock_response

        result = await self.helper.get_merchant_reliability("Walmart")

        assert result is not None
        assert result.merchant_name == "WALMART"
        assert result.total_receipts_processed == 3

        # Calculate expected success rate: 8 valid labels / (8 valid + 2 invalid) = 0.8
        assert abs(result.pattern_only_success_rate - 0.8) < 0.01

        # Check common labels (present in 50%+ of receipts)
        assert "MERCHANT_NAME" in result.common_labels  # Present in all 3
        assert "GRAND_TOTAL" in result.common_labels  # Present in 2/3

        assert result.is_reliable is False  # Only 3 receipts, need 5 minimum

    @pytest.mark.asyncio
    async def test_no_pinecone_data_found(self):
        """Test when no Pinecone data is found for merchant."""
        mock_response = Mock()
        mock_response.matches = []

        self.mock_pinecone_client.query.return_value = mock_response

        result = await self.helper.get_merchant_reliability("Unknown Merchant")

        assert result is None

    @pytest.mark.asyncio
    async def test_merchant_name_normalization(self):
        """Test merchant name normalization."""
        test_cases = [
            ("Walmart", "WALMART"),
            ("WAL-MART", "WALMART"),
            ("WAL MART", "WALMART"),
            ("WALMART #1234", "WALMART"),
            ("McDonald's", "MCDONALD'S"),
            ("MCDONALDS", "MCDONALD'S"),
            ("Target Store #5678", "TARGET"),
        ]

        for input_name, expected in test_cases:
            normalized = self.helper._normalize_merchant_name(input_name)
            assert (
                normalized == expected
            ), f"Expected {expected}, got {normalized} for {input_name}"

    @pytest.mark.asyncio
    async def test_caching_behavior(self):
        """Test merchant data caching."""
        # Mock successful response
        mock_response = Mock()
        mock_response.matches = [
            Mock(
                metadata={
                    "receipt_id": "receipt_1",
                    "merchant_name": "WALMART",
                    "valid_labels": ["MERCHANT_NAME"],
                    "invalid_labels": [],
                    "timestamp": datetime.now().isoformat(),
                }
            )
        ]

        self.mock_pinecone_client.query.return_value = mock_response

        # First call should hit Pinecone
        result1 = await self.helper.get_merchant_reliability("Walmart")
        assert result1 is not None
        assert self.mock_pinecone_client.query.call_count == 1

        # Second call should use cache
        result2 = await self.helper.get_merchant_reliability("Walmart")
        assert result2 is not None
        assert (
            self.mock_pinecone_client.query.call_count == 1
        )  # No additional call

        # Should be the same object from cache
        assert result1.last_updated == result2.last_updated

    @pytest.mark.asyncio
    async def test_cache_refresh(self):
        """Test cache refresh functionality."""
        mock_response = Mock()
        mock_response.matches = [
            Mock(
                metadata={
                    "receipt_id": "receipt_1",
                    "merchant_name": "WALMART",
                    "valid_labels": ["MERCHANT_NAME"],
                    "invalid_labels": [],
                    "timestamp": datetime.now().isoformat(),
                }
            )
        ]

        self.mock_pinecone_client.query.return_value = mock_response

        # First call
        result1 = await self.helper.get_merchant_reliability("Walmart")
        assert self.mock_pinecone_client.query.call_count == 1

        # Refresh cache
        result2 = await self.helper.get_merchant_reliability(
            "Walmart", refresh_cache=True
        )
        assert (
            self.mock_pinecone_client.query.call_count == 2
        )  # Additional call

    @pytest.mark.asyncio
    async def test_pattern_confidence_validation(self):
        """Test pattern confidence validation."""
        mock_response = Mock()
        mock_response.matches = [Mock() for _ in range(15)]  # 15 matches

        self.mock_pinecone_client.query.return_value = mock_response

        confidence_boost = await self.helper.validate_pattern_confidence(
            merchant_name="Walmart",
            label_type="GRAND_TOTAL",
            detected_value="12.99",
            context={},
        )

        # Boost should be based on number of matches: min(0.3, 15/20) = 0.3 (max boost)
        expected_boost = min(0.3, 15 / 20.0)  # Should be 0.3
        assert abs(confidence_boost - expected_boost) < 0.01

    @pytest.mark.asyncio
    async def test_pinecone_error_handling(self):
        """Test error handling in Pinecone queries."""
        # Mock Pinecone error
        self.mock_pinecone_client.query.side_effect = Exception(
            "Pinecone connection error"
        )

        result = await self.helper.get_merchant_reliability("Walmart")

        assert result is None  # Should return None on error, not raise

    @pytest.mark.asyncio
    async def test_cache_ttl_expiration(self):
        """Test cache TTL expiration."""
        # Create a helper with short TTL for testing
        helper = PineconeDecisionHelper(self.mock_pinecone_client, self.config)
        helper._cache_ttl_hours = 0.001  # Very short TTL (about 3.6 seconds)

        mock_response = Mock()
        mock_response.matches = [
            Mock(
                metadata={
                    "receipt_id": "receipt_1",
                    "merchant_name": "WALMART",
                    "valid_labels": ["MERCHANT_NAME"],
                    "invalid_labels": [],
                    "timestamp": datetime.now().isoformat(),
                }
            )
        ]

        self.mock_pinecone_client.query.return_value = mock_response

        # First call
        result1 = await helper.get_merchant_reliability("Walmart")
        assert self.mock_pinecone_client.query.call_count == 1

        # Manually expire cache by setting old timestamp
        cache_key = "walmart"
        if cache_key in helper._reliability_cache:
            old_data = helper._reliability_cache[cache_key]
            old_data.last_updated = datetime.now() - timedelta(
                hours=25
            )  # Expired

        # Second call should hit Pinecone again due to expired cache
        result2 = await helper.get_merchant_reliability("Walmart")
        assert self.mock_pinecone_client.query.call_count == 2

    def test_cache_management(self):
        """Test cache management functions."""
        # Add some test data to cache
        test_data = MerchantReliabilityData(
            merchant_name="Test",
            total_receipts_processed=5,
            pattern_only_success_rate=0.8,
            common_labels=set(),
            rarely_present_labels=set(),
            typical_receipt_structure={},
            last_updated=datetime.now(),
        )

        self.helper._reliability_cache["test"] = test_data

        # Check cache stats
        stats = self.helper.get_cache_stats()
        assert stats["cached_merchants"] == 1
        assert "test" in stats["merchants"]
        assert stats["cache_ttl_hours"] == 24

        # Clear cache
        self.helper.clear_cache()
        stats = self.helper.get_cache_stats()
        assert stats["cached_merchants"] == 0

    @pytest.mark.asyncio
    async def test_pinecone_query_filters(self):
        """Test that proper filters are applied to Pinecone queries."""
        mock_response = Mock()
        mock_response.matches = []

        self.mock_pinecone_client.query.return_value = mock_response

        await self.helper.get_merchant_reliability("Walmart")

        # Verify query was called with proper filters
        call_args = self.mock_pinecone_client.query.call_args
        assert call_args is not None

        # Check that filter includes merchant name
        filter_arg = call_args.kwargs.get("filter", {})
        assert "merchant_name" in filter_arg
        assert filter_arg["merchant_name"] == "WALMART"

        # Check that timestamp filter is applied (last 90 days)
        assert "timestamp" in filter_arg

    def test_analyze_merchant_data_empty(self):
        """Test analysis of empty merchant data."""
        result = self.helper._analyze_merchant_data("Test Merchant", [])

        assert result.merchant_name == "Test Merchant"
        assert result.total_receipts_processed == 0
        assert result.pattern_only_success_rate == 0.0
        assert len(result.common_labels) == 0

    def test_analyze_merchant_data_with_receipts(self):
        """Test analysis of merchant data with multiple receipts."""
        matches = [
            Mock(
                metadata={
                    "receipt_id": "r1",
                    "valid_labels": ["MERCHANT_NAME", "TOTAL"],
                    "invalid_labels": [],
                }
            ),
            Mock(
                metadata={
                    "receipt_id": "r1",  # Same receipt, different word
                    "valid_labels": ["DATE"],
                    "invalid_labels": ["ADDRESS"],
                }
            ),
            Mock(
                metadata={
                    "receipt_id": "r2",
                    "valid_labels": ["MERCHANT_NAME"],
                    "invalid_labels": ["PHONE"],
                }
            ),
        ]

        result = self.helper._analyze_merchant_data("Test", matches)

        assert result.total_receipts_processed == 2  # Unique receipt IDs
        # Success rate: 4 valid / (4 valid + 2 invalid) = 0.667
        assert abs(result.pattern_only_success_rate - 0.667) < 0.01

        # MERCHANT_NAME appears in both receipts (100%), should be common
        assert "MERCHANT_NAME" in result.common_labels
