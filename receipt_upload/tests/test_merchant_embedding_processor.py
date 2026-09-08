"""
Integration tests for MerchantResolvingEmbeddingProcessor.

Tests the merchant enrichment step of the embedding pipeline:
1. Resolve merchant via MerchantResolver
2. Enrich DynamoDB with merchant data

All external services (DynamoDB, S3, OpenAI) are mocked.
"""

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import ReceiptLine, ReceiptWord

from receipt_upload.merchant_resolution import (
    MerchantResolvingEmbeddingProcessor,
    MerchantResult,
)


class TestMerchantResolvingEmbeddingProcessorEnrichment:
    """Test DynamoDB enrichment functionality."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ) as MockDynamo:
            client = MagicMock()
            MockDynamo.return_value = client
            yield client

    @pytest.fixture
    def mock_s3_client(self):
        """Create mock S3 client."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.boto3"
        ) as MockBoto:
            s3 = MagicMock()
            MockBoto.client.return_value = s3
            yield s3

    def test_enrich_receipt_place_updates_dynamo(self, mock_dynamo_client):
        """Test that receipt place data is enriched in DynamoDB."""
        # Setup existing place data
        mock_place = MagicMock()
        mock_place.merchant_name = None
        mock_place.formatted_address = None
        mock_place.phone_number = None
        mock_place.place_id = None
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.boto3"
        ):
            processor = MerchantResolvingEmbeddingProcessor(
                table_name="test-table",
            )

            merchant_result = MerchantResult(
                place_id="ChIJ_test_place",
                merchant_name="Test Store",
                address="123 Main St",
                phone="5551234567",
            )

            processor._enrich_receipt_place(
                image_id="550e8400-e29b-41d4-a716-446655440000",
                receipt_id=1,
                merchant_result=merchant_result,
            )

        mock_dynamo_client.update_receipt_place.assert_called_once()
        call_kwargs = mock_dynamo_client.update_receipt_place.call_args[1]
        assert call_kwargs["place_id"] == "ChIJ_test_place"
        assert call_kwargs["merchant_name"] == "Test Store"
        assert call_kwargs["formatted_address"] == "123 Main St"
        assert call_kwargs["phone_number"] == "5551234567"

    def test_enrich_does_not_overwrite_existing_data(self, mock_dynamo_client):
        """Test that existing merchant data is not overwritten."""
        # Setup existing place data with data
        mock_place = MagicMock()
        mock_place.merchant_name = "Existing Store"
        mock_place.formatted_address = "Existing Address"
        mock_place.phone_number = "1111111111"
        mock_place.place_id = None
        mock_dynamo_client.get_receipt_place.return_value = mock_place

        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.boto3"
        ):
            processor = MerchantResolvingEmbeddingProcessor(
                table_name="test-table",
            )

            merchant_result = MerchantResult(
                place_id="ChIJ_new_place",
                merchant_name="New Store",
                address="New Address",
                phone="5551234567",
            )

            processor._enrich_receipt_place(
                image_id="550e8400-e29b-41d4-a716-446655440000",
                receipt_id=1,
                merchant_result=merchant_result,
            )

        mock_dynamo_client.update_receipt_place.assert_called_once()
        call_kwargs = mock_dynamo_client.update_receipt_place.call_args[1]
        # place_id should be updated
        assert call_kwargs["place_id"] == "ChIJ_new_place"
        # Existing data should not be overwritten
        assert "merchant_name" not in call_kwargs
        assert "formatted_address" not in call_kwargs
        assert "phone_number" not in call_kwargs

    def test_enrich_handles_missing_place_data(self, mock_dynamo_client):
        """Test handling when no existing place data found."""
        mock_dynamo_client.get_receipt_place.return_value = None

        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.boto3"
        ):
            processor = MerchantResolvingEmbeddingProcessor(
                table_name="test-table",
            )

            merchant_result = MerchantResult(
                place_id="ChIJ_test_place",
                merchant_name="Test Store",
            )

            # Should not raise
            processor._enrich_receipt_place(
                image_id="550e8400-e29b-41d4-a716-446655440000",
                receipt_id=1,
                merchant_result=merchant_result,
            )

        # Should not call update when no place data exists
        mock_dynamo_client.update_receipt_place.assert_not_called()
        # Should call add to create new place
        mock_dynamo_client.add_receipt_place.assert_called_once()
        call_args = mock_dynamo_client.add_receipt_place.call_args[0]
        new_place = call_args[0]
        assert new_place.place_id == "ChIJ_test_place"
        assert new_place.merchant_name == "Test Store"


class TestMerchantResolvingEmbeddingProcessorInit:
    """Test processor initialization."""

    def test_init_with_places_client(self):
        """Test initialization with Places API key."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ):
            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.boto3"
            ):
                # Patch the actual import location for PlacesClient
                with patch("receipt_places.PlacesClient") as MockPlaces:
                    mock_places = MagicMock()
                    MockPlaces.return_value = mock_places

                    processor = MerchantResolvingEmbeddingProcessor(
                        table_name="test-table",
                        google_places_api_key="test-api-key",
                    )

                    MockPlaces.assert_called_once_with(api_key="test-api-key")
                    assert processor.places_client == mock_places

    def test_init_without_places_client(self):
        """Test initialization without Places API key."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ):
            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.boto3"
            ):
                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                )

                assert processor.places_client is None


class TestEnrichReceiptPlacePersistsConfidence:
    """New ReceiptPlace records must persist the resolver's match-quality
    signals (confidence / validation_status / matched_fields), not drop them
    to defaults (the bug that stored every similarity place as confidence=0.0).
    """

    @pytest.fixture
    def mock_dynamo_client(self):
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ) as MockDynamo:
            client = MagicMock()
            MockDynamo.return_value = client
            yield client

    def _create_place(self, mock_dynamo_client, confidence):
        # No existing place -> exercises the create path.
        mock_dynamo_client.get_receipt_place.return_value = None
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.boto3"
        ):
            processor = MerchantResolvingEmbeddingProcessor(
                table_name="test-table",
            )
            processor._enrich_receipt_place(
                image_id="550e8400-e29b-41d4-a716-446655440000",
                receipt_id=1,
                merchant_result=MerchantResult(
                    place_id="ChIJ_poke",
                    merchant_name="Poke Market",
                    address="6815 Tom Rodriguez St, Las Vegas, NV 89113",
                    phone=None,
                    confidence=confidence,
                    resolution_tier="similarity_text",
                ),
            )
        mock_dynamo_client.add_receipt_place.assert_called_once()
        return mock_dynamo_client.add_receipt_place.call_args[0][0]

    def test_create_persists_confidence_and_matched_fields(
        self, mock_dynamo_client
    ):
        place = self._create_place(mock_dynamo_client, confidence=0.79)
        assert place.confidence == 0.79
        # 0.79 < 0.8 -> UNSURE (mirrors fix-place lambda semantics)
        assert place.validation_status == "UNSURE"
        # phone was None, so only name + address are recorded
        assert "merchant_name" in place.matched_fields
        assert "address" in place.matched_fields
        assert "phone" not in place.matched_fields

    def test_create_high_confidence_is_matched(self, mock_dynamo_client):
        place = self._create_place(mock_dynamo_client, confidence=0.9)
        assert place.confidence == 0.9
        assert place.validation_status == "MATCHED"
