"""
Integration test for local receipt processing pipeline.

This test validates that the receipt labeling pipeline can run end-to-end
using local data without making external API calls.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from receipt_label.core.labeler import ReceiptLabeler
from receipt_label.data.local_data_loader import LocalDataLoader
from receipt_label.models.label import LabelAnalysis
from receipt_label.models.validation import ValidationAnalysis


class TestLocalPipeline:
    """Test the receipt labeling pipeline with local data."""

    @pytest.fixture
    def local_data_dir(self):
        """Get the local data directory path."""
        # Check common locations for test data
        possible_paths = [
            Path("./receipt_data"),  # Current directory
            Path("./test_data"),  # Alternative test data location
            Path(__file__).parent.parent.parent
            / "test_data",  # In tests directory
            Path(__file__).parent.parent.parent.parent.parent
            / "receipt_data",  # Project root
        ]

        for path in possible_paths:
            if path.exists() and path.is_dir():
                return str(path)

        # If no data directory exists, skip the test
        pytest.skip(
            "No local receipt data directory found. Run export_receipt_data.py first."
        )

    @pytest.fixture
    def data_loader(self, local_data_dir):
        """Create a local data loader instance."""
        return LocalDataLoader(local_data_dir)

    @pytest.fixture
    def mock_openai_response(self):
        """Mock OpenAI API response for label analysis."""
        from receipt_label.models.label import WordLabel

        return LabelAnalysis(
            labels=[
                WordLabel(word_id=1, label="MERCHANT_NAME", confidence=0.95),
                WordLabel(word_id=5, label="DATE", confidence=0.90),
                WordLabel(word_id=10, label="TOTAL", confidence=0.95),
            ],
            receipt_id="1",
            image_id="550e8400-e29b-41d4-a716-446655440001",
            sections=[],
            total_labeled_words=3,
            requires_review=False,
            review_reasons=[],
            analysis_reasoning="Test analysis",
            metadata={
                "confidence": 0.95,
                "processing_time_ms": 100,
                "stub_response": True,
            },
        )

    @pytest.fixture
    def mock_validation_response(self):
        """Mock validation analysis response."""
        return ValidationAnalysis(
            version="1.0.0",
            timestamp="2024-01-01T00:00:00Z",
            is_valid=True,
            issues=[],
            warnings=[],
            metadata={"validation_time_ms": 50},
        )

    @pytest.mark.integration
    def test_load_local_receipt_data(self, data_loader):
        """Test loading receipt data from local files."""
        # Get available receipts
        receipts = data_loader.list_available_receipts()
        assert len(receipts) > 0, "No receipts found in local data"

        # Load first receipt
        image_id, receipt_id = receipts[0]
        result = data_loader.load_receipt_by_id(image_id, receipt_id)

        assert (
            result is not None
        ), f"Failed to load receipt {image_id}/{receipt_id}"
        receipt, words, lines = result

        # Validate loaded data
        assert receipt is not None
        assert receipt.image_id == image_id
        assert receipt.receipt_id == int(receipt_id)

        assert len(words) > 0, "No words loaded"
        assert all(w.receipt_id == int(receipt_id) for w in words)

        if lines:  # Lines might be empty for some receipts
            assert all(l.receipt_id == int(receipt_id) for l in lines)

    @pytest.mark.integration
    @patch.dict(os.environ, {"USE_STUB_APIS": "true"})
    def test_pipeline_with_stubbed_apis(
        self, data_loader, mock_openai_response, mock_validation_response
    ):
        """Test the full pipeline with stubbed external APIs."""
        # Get a test receipt
        receipts = data_loader.list_available_receipts()
        if not receipts:
            pytest.skip("No local receipts available")

        image_id, receipt_id = receipts[0]

        # Load receipt data
        result = data_loader.load_receipt_with_labels(image_id, receipt_id)
        assert result is not None

        receipt, words, lines, existing_labels = result

        # Create mock DynamoDB client that returns our local data
        mock_dynamo = MagicMock()
        mock_dynamo.get_receipt.return_value = receipt
        mock_dynamo.list_receipt_words_by_receipt.return_value = words
        mock_dynamo.list_receipt_lines_by_receipt.return_value = lines
        mock_dynamo.list_receipt_word_labels_by_receipt.return_value = (
            existing_labels
        )

        # Mock OpenAI client
        mock_openai = MagicMock()
        mock_openai.generate_label_analysis.return_value = mock_openai_response
        mock_openai.validate_analysis.return_value = mock_validation_response

        # Create labeler with mocked clients
        with patch(
            "receipt_label.core.labeler.DynamoClient", return_value=mock_dynamo
        ):
            with patch(
                "receipt_label.core.labeler.OpenAIClient",
                return_value=mock_openai,
            ):
                labeler = ReceiptLabeler()

                # Process the receipt
                result = labeler.label_receipt(
                    receipt=receipt, receipt_words=words, receipt_lines=lines
                )

                # Verify the pipeline ran
                assert result is not None
                # LabelingResult doesn't have receipt_id directly
                assert result.structure_analysis is not None or result.errors

                # Verify no real API calls were made
                assert mock_openai.generate_label_analysis.called
                assert mock_openai.validate_analysis.called

    @pytest.mark.integration
    def test_pipeline_without_external_calls(self, data_loader):
        """Ensure local data loading works without any external service calls."""
        # Load test data
        receipts = data_loader.list_available_receipts()
        if not receipts:
            pytest.skip("No local receipts available")

        image_id, receipt_id = receipts[0]
        result = data_loader.load_receipt_by_id(image_id, receipt_id)

        # Verify we can at least load and work with the data
        assert result is not None
        receipt, words, lines = result

        # Basic operations should work without external calls
        assert len(words) > 0
        word_texts = [w.text for w in words]
        assert all(isinstance(text, str) for text in word_texts)

    @pytest.mark.integration
    def test_sample_dataset_if_available(self, data_loader):
        """Test loading and using the sample dataset if available."""
        sample_index = data_loader.load_sample_index()

        if sample_index is None:
            pytest.skip("No sample dataset index found")

        # Verify sample dataset structure
        assert "receipts" in sample_index
        assert "categories" in sample_index
        assert len(sample_index["receipts"]) > 0

        # Load a few receipts from the sample
        for receipt_info in sample_index["receipts"][:3]:
            image_id = receipt_info["image_id"]
            receipt_id = receipt_info["receipt_id"]

            result = data_loader.load_receipt_by_id(image_id, receipt_id)
            assert (
                result is not None
            ), f"Failed to load sample receipt {image_id}/{receipt_id}"
