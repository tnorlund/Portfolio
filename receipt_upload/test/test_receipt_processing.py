"""Comprehensive unit tests for receipt processing modules."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
)

from receipt_upload.receipt_processing.receipt import refine_receipt


class TestReceiptProcessing:
    """Test cases for receipt.py module."""

    @pytest.fixture
    def mock_receipt_lines(self):
        """Create mock receipt lines."""
        lines = []
        for i in range(3):
            line = Mock(spec=ReceiptLine)
            line.image_id = "test-image"
            line.receipt_id = 1
            line.line_id = i + 1
            line.text = f"Line {i + 1}"
            lines.append(line)
        return lines

    @pytest.fixture
    def mock_receipt_words(self):
        """Create mock receipt words."""
        words = []
        for i in range(5):
            word = Mock(spec=ReceiptWord)
            word.image_id = "test-image"
            word.receipt_id = 1
            word.line_id = (i // 2) + 1
            word.word_id = i + 1
            word.text = f"Word{i + 1}"
            words.append(word)
        return words

    @pytest.fixture
    def mock_receipt_letters(self):
        """Create mock receipt letters."""
        letters = []
        for i in range(10):
            letter = Mock(spec=ReceiptLetter)
            letter.image_id = "test-image"
            letter.receipt_id = 1
            letter.line_id = (i // 5) + 1
            letter.word_id = (i // 2) + 1
            letter.letter_id = i + 1
            letter.text = chr(65 + i)  # A, B, C, etc.
            letters.append(letter)
        return letters

    @pytest.fixture
    def mock_ocr_routing_decision(self):
        """Create mock OCR routing decision."""
        decision = Mock(spec=OCRRoutingDecision)
        decision.image_id = "test-image"
        decision.status = OCRStatus.PENDING.value
        decision.receipt_count = 0
        decision.updated_at = datetime.now(timezone.utc)
        return decision

    @pytest.mark.unit
    def test_refine_receipt_basic(
        self,
        mock_receipt_lines,
        mock_receipt_words,
        mock_receipt_letters,
        mock_ocr_routing_decision,
    ):
        """Test basic receipt refinement."""
        with patch(
            "receipt_upload.receipt_processing.receipt.DynamoClient"
        ) as MockDynamoClient:
            mock_client = Mock()
            MockDynamoClient.return_value = mock_client

            # Call the function
            refine_receipt(
                "test-table",
                mock_receipt_lines,
                mock_receipt_words,
                mock_receipt_letters,
                mock_ocr_routing_decision,
            )

            # Verify DynamoDB operations
            MockDynamoClient.assert_called_once_with("test-table")
            mock_client.addReceiptLines.assert_called_once_with(
                mock_receipt_lines
            )
            mock_client.addReceiptWords.assert_called_once_with(
                mock_receipt_words
            )
            mock_client.addReceiptLetters.assert_called_once_with(
                mock_receipt_letters
            )

            # Verify routing decision update
            assert (
                mock_ocr_routing_decision.status == OCRStatus.COMPLETED.value
            )
            assert mock_ocr_routing_decision.receipt_count == 1
            mock_client.updateOCRRoutingDecision.assert_called_once_with(
                mock_ocr_routing_decision
            )

    @pytest.mark.unit
    def test_refine_receipt_empty_data(self, mock_ocr_routing_decision):
        """Test refining receipt with empty OCR data."""
        with patch(
            "receipt_upload.receipt_processing.receipt.DynamoClient"
        ) as MockDynamoClient:
            mock_client = Mock()
            MockDynamoClient.return_value = mock_client

            refine_receipt(
                "test-table",
                [],  # Empty lines
                [],  # Empty words
                [],  # Empty letters
                mock_ocr_routing_decision,
            )

            # Should still call all methods, even with empty lists
            mock_client.addReceiptLines.assert_called_once_with([])
            mock_client.addReceiptWords.assert_called_once_with([])
            mock_client.addReceiptLetters.assert_called_once_with([])

            # Status should still be updated
            assert (
                mock_ocr_routing_decision.status == OCRStatus.COMPLETED.value
            )

    @pytest.mark.unit
    def test_refine_receipt_timestamp_update(self, mock_ocr_routing_decision):
        """Test that timestamp is properly updated."""
        original_time = mock_ocr_routing_decision.updated_at

        with patch(
            "receipt_upload.receipt_processing.receipt.DynamoClient"
        ) as MockDynamoClient:
            mock_client = Mock()
            MockDynamoClient.return_value = mock_client

            # Mock datetime to control the timestamp
            with patch(
                "receipt_upload.receipt_processing.receipt.datetime"
            ) as mock_datetime:
                new_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
                mock_datetime.now.return_value = new_time
                mock_datetime.timezone.utc = timezone.utc

                refine_receipt(
                    "test-table", [], [], [], mock_ocr_routing_decision
                )

                assert mock_ocr_routing_decision.updated_at == new_time
                assert mock_ocr_routing_decision.updated_at != original_time


class TestPhotoProcessing:
    """Test cases for photo.py module."""

    @pytest.mark.unit
    def test_photo_processing_imports(self):
        """Test that photo processing module can be imported."""
        try:
            from receipt_upload.receipt_processing import photo

            assert True
        except ImportError:
            pytest.fail("Failed to import photo processing module")


class TestNativeProcessing:
    """Test cases for native.py module."""

    @pytest.mark.unit
    def test_native_processing_imports(self):
        """Test that native processing module can be imported."""
        try:
            from receipt_upload.receipt_processing import native

            assert True
        except ImportError:
            pytest.fail("Failed to import native processing module")


class TestScanProcessing:
    """Test cases for scan.py module."""

    @pytest.mark.unit
    def test_scan_processing_imports(self):
        """Test that scan processing module can be imported."""
        try:
            from receipt_upload.receipt_processing import scan

            assert True
        except ImportError:
            pytest.fail("Failed to import scan processing module")


# Additional test fixtures for comprehensive receipt processing tests
@pytest.fixture
def sample_ocr_job():
    """Create a sample OCR job for testing."""
    job = Mock(spec=OCRJob)
    job.job_id = "test-job-123"
    job.status = OCRStatus.PENDING.value
    job.image_count = 1
    job.created_at = datetime.now(timezone.utc)
    job.updated_at = datetime.now(timezone.utc)
    return job


@pytest.fixture
def sample_receipt_data():
    """Create sample receipt data structure."""
    return {
        "receipt_id": 1,
        "image_id": "550e8400-e29b-41d4-a716-446655440003",
        "merchant": "Test Store",
        "date": "2024-01-01",
        "total": 99.99,
        "items": [
            {"name": "Item 1", "price": 49.99},
            {"name": "Item 2", "price": 50.00},
        ],
    }


class TestReceiptProcessingIntegration:
    """Integration tests for receipt processing workflow."""

    @pytest.mark.integration
    def test_full_receipt_processing_workflow(
        self, sample_ocr_job, sample_receipt_data
    ):
        """Test complete receipt processing workflow."""
        with patch(
            "receipt_upload.receipt_processing.receipt.DynamoClient"
        ) as MockDynamoClient:
            mock_client = Mock()
            MockDynamoClient.return_value = mock_client

            # Create mock OCR data
            lines = []
            words = []
            letters = []

            # Create line for merchant name
            merchant_line = Mock(spec=ReceiptLine)
            merchant_line.text = sample_receipt_data["merchant"]
            merchant_line.line_id = 1
            merchant_line.receipt_id = sample_receipt_data["receipt_id"]
            merchant_line.image_id = sample_receipt_data["image_id"]
            lines.append(merchant_line)

            # Create words for items
            for idx, item in enumerate(sample_receipt_data["items"]):
                word = Mock(spec=ReceiptWord)
                word.text = item["name"]
                word.word_id = idx + 1
                word.line_id = idx + 2
                word.receipt_id = sample_receipt_data["receipt_id"]
                word.image_id = sample_receipt_data["image_id"]
                words.append(word)

            # Create routing decision
            routing_decision = Mock(spec=OCRRoutingDecision)
            routing_decision.image_id = sample_receipt_data["image_id"]
            routing_decision.status = OCRStatus.PENDING.value

            # Process the receipt
            refine_receipt(
                "test-table", lines, words, letters, routing_decision
            )

            # Verify all components were processed
            assert mock_client.addReceiptLines.called
            assert mock_client.addReceiptWords.called
            assert routing_decision.status == OCRStatus.COMPLETED.value
            assert routing_decision.receipt_count == 1

    @pytest.mark.integration
    def test_receipt_processing_error_handling(self):
        """Test error handling in receipt processing."""
        with patch(
            "receipt_upload.receipt_processing.receipt.DynamoClient"
        ) as MockDynamoClient:
            mock_client = Mock()
            MockDynamoClient.return_value = mock_client

            # Make DynamoDB operations fail
            mock_client.addReceiptLines.side_effect = Exception(
                "DynamoDB error"
            )

            routing_decision = Mock(spec=OCRRoutingDecision)
            routing_decision.status = OCRStatus.PENDING.value

            # Should raise the exception
            with pytest.raises(Exception, match="DynamoDB error"):
                refine_receipt(
                    "test-table",
                    [Mock(spec=ReceiptLine)],
                    [],
                    [],
                    routing_decision,
                )

            # Status should not be updated on error
            assert routing_decision.status != OCRStatus.COMPLETED.value
