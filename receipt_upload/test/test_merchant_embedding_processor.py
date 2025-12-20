"""
Integration tests for MerchantResolvingEmbeddingProcessor.

Tests the full embedding and merchant resolution pipeline:
1. Generate embeddings via receipt_chroma
2. Resolve merchant via MerchantResolver
3. Enrich DynamoDB with merchant data

All external services (ChromaDB, DynamoDB, S3, OpenAI) are mocked.
"""

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from receipt_upload.merchant_resolution import (
    MerchantResolvingEmbeddingProcessor,
    MerchantResult,
)

from receipt_dynamo.entities import ReceiptLine, ReceiptWord


class TestMerchantResolvingEmbeddingProcessor:
    """Test MerchantResolvingEmbeddingProcessor."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create mock DynamoDB client."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ) as MockDynamo:
            client = MagicMock()
            client.list_receipt_lines_from_receipt.return_value = []
            client.list_receipt_words_from_receipt.return_value = []
            client.list_receipt_word_labels_for_receipt.return_value = (
                [],
                None,
            )
            client.get_receipt_place.return_value = None
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

    @pytest.fixture
    def mock_embedding_result(self):
        """Create mock EmbeddingResult."""
        result = MagicMock()
        result.lines_client = MagicMock()
        result.words_client = MagicMock()
        result.compaction_run = MagicMock()
        result.compaction_run.run_id = "test-run-123"
        result.close = MagicMock()
        return result

    @pytest.fixture
    def sample_lines(self):
        """Create sample ReceiptLine entities."""
        return [
            MagicMock(
                spec=ReceiptLine,
                image_id="test-image",
                receipt_id=1,
                line_id=1,
                text="Coffee Shop",
            ),
            MagicMock(
                spec=ReceiptLine,
                image_id="test-image",
                receipt_id=1,
                line_id=2,
                text="123 Main St",
            ),
        ]

    @pytest.fixture
    def sample_words(self):
        """Create sample ReceiptWord entities."""
        return [
            MagicMock(
                spec=ReceiptWord,
                image_id="test-image",
                receipt_id=1,
                line_id=1,
                word_id=1,
                text="Coffee",
                extracted_data={},
            ),
            MagicMock(
                spec=ReceiptWord,
                image_id="test-image",
                receipt_id=1,
                line_id=1,
                word_id=2,
                text="Shop",
                extracted_data={},
            ),
            MagicMock(
                spec=ReceiptWord,
                image_id="test-image",
                receipt_id=1,
                line_id=2,
                word_id=3,
                text="(555) 123-4567",
                extracted_data={"type": "phone", "value": "5551234567"},
            ),
        ]

    def test_process_embeddings_success_with_merchant(
        self,
        mock_dynamo_client,
        mock_s3_client,
        mock_embedding_result,
        sample_lines,
        sample_words,
    ):
        """Test successful processing with merchant resolution."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.return_value = mock_embedding_result

            # Mock MerchantResolver
            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.MerchantResolver"
            ) as MockResolver:
                mock_resolver = MagicMock()
                mock_resolver.resolve.return_value = MerchantResult(
                    place_id="ChIJ_test_place",
                    merchant_name="Coffee Shop Inc",
                    address="123 Main St",
                    phone="5551234567",
                    confidence=0.95,
                    resolution_tier="phone",
                )
                MockResolver.return_value = mock_resolver

                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                )

                result = processor.process_embeddings(
                    image_id="test-image",
                    receipt_id=1,
                    lines=sample_lines,
                    words=sample_words,
                )

        assert result["success"] is True
        assert result["merchant_found"] is True
        assert result["merchant_name"] == "Coffee Shop Inc"
        assert result["merchant_place_id"] == "ChIJ_test_place"
        assert result["merchant_resolution_tier"] == "phone"
        assert result["run_id"] == "test-run-123"
        mock_embedding_result.close.assert_called_once()

    def test_process_embeddings_success_without_merchant(
        self,
        mock_dynamo_client,
        mock_s3_client,
        mock_embedding_result,
        sample_lines,
        sample_words,
    ):
        """Test successful processing when no merchant found."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.return_value = mock_embedding_result

            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.MerchantResolver"
            ) as MockResolver:
                mock_resolver = MagicMock()
                mock_resolver.resolve.return_value = MerchantResult()
                MockResolver.return_value = mock_resolver

                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                )

                result = processor.process_embeddings(
                    image_id="test-image",
                    receipt_id=1,
                    lines=sample_lines,
                    words=sample_words,
                )

        assert result["success"] is True
        assert result["merchant_found"] is False
        assert result["merchant_place_id"] is None
        mock_embedding_result.close.assert_called_once()

    def test_process_embeddings_fetches_data_when_not_provided(
        self,
        mock_dynamo_client,
        mock_s3_client,
        mock_embedding_result,
    ):
        """Test that lines/words are fetched from DynamoDB when not provided."""
        mock_dynamo_client.list_receipt_lines_from_receipt.return_value = [
            MagicMock(spec=ReceiptLine)
        ]
        mock_dynamo_client.list_receipt_words_from_receipt.return_value = [
            MagicMock(spec=ReceiptWord, extracted_data={})
        ]

        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.return_value = mock_embedding_result

            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.MerchantResolver"
            ) as MockResolver:
                mock_resolver = MagicMock()
                mock_resolver.resolve.return_value = MerchantResult()
                MockResolver.return_value = mock_resolver

                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                )

                result = processor.process_embeddings(
                    image_id="test-image",
                    receipt_id=1,
                    lines=None,
                    words=None,
                )

        mock_dynamo_client.list_receipt_lines_from_receipt.assert_called_once_with(
            "test-image", 1
        )
        mock_dynamo_client.list_receipt_words_from_receipt.assert_called_once_with(
            "test-image", 1
        )
        assert result["success"] is True

    def test_process_embeddings_handles_embedding_failure(
        self,
        mock_dynamo_client,
        mock_s3_client,
        sample_lines,
        sample_words,
    ):
        """Test handling of embedding creation failure."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.side_effect = Exception("OpenAI API error")

            processor = MerchantResolvingEmbeddingProcessor(
                table_name="test-table",
                chromadb_bucket="test-bucket",
            )

            result = processor.process_embeddings(
                image_id="test-image",
                receipt_id=1,
                lines=sample_lines,
                words=sample_words,
            )

        assert result["success"] is False
        assert "OpenAI API error" in result["error"]
        assert result["merchant_found"] is False

    def test_process_embeddings_handles_merchant_resolution_failure(
        self,
        mock_dynamo_client,
        mock_s3_client,
        mock_embedding_result,
        sample_lines,
        sample_words,
    ):
        """Test handling of merchant resolution failure."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.return_value = mock_embedding_result

            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.MerchantResolver"
            ) as MockResolver:
                mock_resolver = MagicMock()
                mock_resolver.resolve.side_effect = Exception(
                    "Resolution failed"
                )
                MockResolver.return_value = mock_resolver

                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                )

                result = processor.process_embeddings(
                    image_id="test-image",
                    receipt_id=1,
                    lines=sample_lines,
                    words=sample_words,
                )

        # Should still succeed - embeddings were created
        assert result["success"] is True
        assert result["merchant_found"] is False
        mock_embedding_result.close.assert_called_once()

    def test_process_embeddings_closes_clients_on_error(
        self,
        mock_dynamo_client,
        mock_s3_client,
        mock_embedding_result,
        sample_lines,
        sample_words,
    ):
        """Test that clients are closed even when resolution fails."""
        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.create_embeddings_and_compaction_run"
        ) as mock_create:
            mock_create.return_value = mock_embedding_result

            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.MerchantResolver"
            ) as MockResolver:
                mock_resolver = MagicMock()
                mock_resolver.resolve.side_effect = Exception("Error")
                MockResolver.return_value = mock_resolver

                processor = MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                )

                processor.process_embeddings(
                    image_id="test-image",
                    receipt_id=1,
                    lines=sample_lines,
                    words=sample_words,
                )

        # Verify close was called
        mock_embedding_result.close.assert_called_once()


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
                chromadb_bucket="test-bucket",
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
                chromadb_bucket="test-bucket",
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
                chromadb_bucket="test-bucket",
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
                        chromadb_bucket="test-bucket",
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
                    chromadb_bucket="test-bucket",
                )

                assert processor.places_client is None

    def test_init_sets_queue_urls_in_environment(self, monkeypatch):
        """Test that queue URLs are set in environment."""
        import os

        # Use monkeypatch to set env vars (auto-restores after test)
        monkeypatch.delenv("CHROMADB_LINES_QUEUE_URL", raising=False)
        monkeypatch.delenv("CHROMADB_WORDS_QUEUE_URL", raising=False)

        with patch(
            "receipt_upload.merchant_resolution.embedding_processor.DynamoClient"
        ):
            with patch(
                "receipt_upload.merchant_resolution.embedding_processor.boto3"
            ):
                MerchantResolvingEmbeddingProcessor(
                    table_name="test-table",
                    chromadb_bucket="test-bucket",
                    lines_queue_url="https://sqs.us-east-1.amazonaws.com/lines",
                    words_queue_url="https://sqs.us-east-1.amazonaws.com/words",
                )

        assert (
            os.environ.get("CHROMADB_LINES_QUEUE_URL")
            == "https://sqs.us-east-1.amazonaws.com/lines"
        )
        assert (
            os.environ.get("CHROMADB_WORDS_QUEUE_URL")
            == "https://sqs.us-east-1.amazonaws.com/words"
        )
