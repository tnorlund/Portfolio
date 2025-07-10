"""Integration tests for the complete noise word handling pipeline."""

import json
from datetime import datetime
from typing import List
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.client import DynamoClient
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import Receipt, ReceiptWord

from receipt_label.embedding.word.submit import (
    list_receipt_words_with_no_embeddings,
)
from receipt_label.utils.noise_detection import is_noise_word


class TestNoiseWordPipeline:
    """Test the complete noise word detection and filtering pipeline."""

    @pytest.fixture
    def mock_dynamo_client(self):
        """Create a mock DynamoDB client."""
        client = MagicMock(spec=DynamoClient)
        client.table_name = "test-table"
        return client

    @pytest.fixture
    def sample_receipt_words(self) -> List[ReceiptWord]:
        """Create sample receipt words with mix of noise and meaningful content."""
        return [
            # Meaningful words
            ReceiptWord(
                receipt_id=1,
                image_id="test-image-001",
                line_id=1,
                word_id=1,
                text="TOTAL",
                bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 100, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 100, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
                embedding_status=EmbeddingStatus.NONE,
                is_noise=False,
            ),
            ReceiptWord(
                receipt_id=1,
                image_id="test-image-001",
                line_id=1,
                word_id=2,
                text="$15.99",
                bounding_box={"x": 120, "y": 0, "width": 80, "height": 20},
                top_left={"x": 120, "y": 0},
                top_right={"x": 200, "y": 0},
                bottom_left={"x": 120, "y": 20},
                bottom_right={"x": 200, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.98,
                embedding_status=EmbeddingStatus.NONE,
                is_noise=False,
            ),
            # Noise words
            ReceiptWord(
                receipt_id=1,
                image_id="test-image-001",
                line_id=2,
                word_id=1,
                text=".",
                bounding_box={"x": 0, "y": 30, "width": 10, "height": 10},
                top_left={"x": 0, "y": 30},
                top_right={"x": 10, "y": 30},
                bottom_left={"x": 0, "y": 40},
                bottom_right={"x": 10, "y": 40},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.85,
                embedding_status=EmbeddingStatus.NONE,
                is_noise=True,
            ),
            ReceiptWord(
                receipt_id=1,
                image_id="test-image-001",
                line_id=2,
                word_id=2,
                text="|",
                bounding_box={"x": 20, "y": 30, "width": 10, "height": 20},
                top_left={"x": 20, "y": 30},
                top_right={"x": 30, "y": 30},
                bottom_left={"x": 20, "y": 50},
                bottom_right={"x": 30, "y": 50},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.75,
                embedding_status=EmbeddingStatus.NONE,
                is_noise=True,
            ),
            ReceiptWord(
                receipt_id=1,
                image_id="test-image-001",
                line_id=3,
                word_id=1,
                text="---",
                bounding_box={"x": 0, "y": 60, "width": 200, "height": 5},
                top_left={"x": 0, "y": 60},
                top_right={"x": 200, "y": 60},
                bottom_left={"x": 0, "y": 65},
                bottom_right={"x": 200, "y": 65},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.65,
                embedding_status=EmbeddingStatus.NONE,
                is_noise=True,
            ),
        ]

    @pytest.mark.integration
    def test_ocr_to_storage_pipeline(self, mock_dynamo_client):
        """Test that OCR processing correctly identifies and marks noise words."""
        from receipt_upload.ocr import process_ocr_dict_as_receipt

        # Mock OCR response
        ocr_response = {
            "receipt_id": 1,
            "lines": [
                {
                    "line_id": 1,
                    "words": [
                        {
                            "word_id": 1,
                            "text": "SUBTOTAL",
                            "confidence": 0.95,
                            "bounding_box": {
                                "x": 0,
                                "y": 0,
                                "width": 100,
                                "height": 20,
                            },
                        },
                        {
                            "word_id": 2,
                            "text": ":",
                            "confidence": 0.90,
                            "bounding_box": {
                                "x": 110,
                                "y": 0,
                                "width": 10,
                                "height": 20,
                            },
                        },
                        {
                            "word_id": 3,
                            "text": "$12.50",
                            "confidence": 0.98,
                            "bounding_box": {
                                "x": 130,
                                "y": 0,
                                "width": 70,
                                "height": 20,
                            },
                        },
                    ],
                },
                {
                    "line_id": 2,
                    "words": [
                        {
                            "word_id": 1,
                            "text": "===",
                            "confidence": 0.70,
                            "bounding_box": {
                                "x": 0,
                                "y": 30,
                                "width": 200,
                                "height": 5,
                            },
                        },
                    ],
                },
            ],
        }

        # Process the OCR response
        with patch("receipt_upload.ocr.get_client_manager") as mock_get_client:
            mock_client_manager = MagicMock()
            mock_client_manager.dynamo = mock_dynamo_client
            mock_get_client.return_value = mock_client_manager

            receipt, words = process_ocr_dict_as_receipt(
                ocr_response, "test-image-001"
            )

            # Verify noise detection
            assert words[0].text == "SUBTOTAL"
            assert words[0].is_noise is False  # Meaningful word

            assert words[1].text == ":"
            assert words[1].is_noise is True  # Punctuation

            assert words[2].text == "$12.50"
            assert words[2].is_noise is False  # Currency amount

            assert words[3].text == "==="
            assert words[3].is_noise is True  # Separator

    @pytest.mark.integration
    def test_embedding_pipeline_filtering(
        self, mock_dynamo_client, sample_receipt_words
    ):
        """Test that embedding pipeline correctly filters out noise words."""
        # Mock DynamoDB to return our sample words
        mock_dynamo_client.list_receipt_words_by_embedding_status.return_value = (
            sample_receipt_words
        )

        with patch(
            "receipt_label.embedding.word.submit.get_client_manager"
        ) as mock_get_client:
            mock_client_manager = MagicMock()
            mock_client_manager.dynamo = mock_dynamo_client
            mock_get_client.return_value = mock_client_manager

            # Get words for embedding
            words_to_embed = list_receipt_words_with_no_embeddings()

            # Should only include non-noise words
            assert len(words_to_embed) == 2
            assert all(not word.is_noise for word in words_to_embed)
            assert words_to_embed[0].text == "TOTAL"
            assert words_to_embed[1].text == "$15.99"

    @pytest.mark.integration
    def test_backward_compatibility(self, mock_dynamo_client):
        """Test that existing receipt words without is_noise field are handled correctly."""
        # Create old-style DynamoDB items without is_noise field
        old_items = [
            {
                "receipt_id": {"N": "1"},
                "image_id": {"S": "test-image-001"},
                "line_id": {"N": "1"},
                "word_id": {"N": "1"},
                "text": {"S": "LEGACY"},
                "bounding_box": {
                    "M": {
                        "x": {"N": "0"},
                        "y": {"N": "0"},
                        "width": {"N": "100"},
                        "height": {"N": "20"},
                    }
                },
                "confidence": {"N": "0.95"},
                "embedding_status": {"S": "NONE"},
                # Note: no is_noise field
            }
        ]

        # Mock query response
        mock_dynamo_client.query.return_value = {"Items": old_items}

        # Test deserialization
        from receipt_dynamo.entities.receipt_word import item_to_receipt_word

        word = item_to_receipt_word(old_items[0])

        # Should default to False for backward compatibility
        assert word.is_noise is False
        assert word.text == "LEGACY"

    @pytest.mark.integration
    def test_noise_detection_performance(self):
        """Test performance of noise detection on realistic receipt data."""
        import time

        # Sample of realistic receipt text
        receipt_texts = [
            "WALMART",
            "SUPERCENTER",
            "#5260",
            "MANAGER",
            "TOM",
            "SMITH",
            "979-123-4567",
            "ST#",
            "5260",
            "OP#",
            "00001234",
            "TE#",
            "25",
            "TR#",
            "0123",
            "GROCERY",
            "BANANAS",
            "0.68",
            "LB",
            "@",
            "$0.29/LB",
            "$0.20",
            "F",
            "MILK",
            "2%",
            "GAL",
            "$3.49",
            "F",
            "BREAD",
            "WHEAT",
            "$2.99",
            "F",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "-",
            "SUBTOTAL",
            "$6.68",
            "TAX",
            "1",
            "@",
            "8.250%",
            "$0.55",
            "TOTAL",
            "$7.23",
            "CASH",
            "TEND",
            "$10.00",
            "CHANGE",
            "DUE",
            "$2.77",
            "****",
            "****",
            "****",
            "1234",
            "|",
            "|",
            "|",
            "|",
            "|",
            ".",
            ".",
            ".",
            ".",
            ".",
            "===",
            "===",
            "===",
            "===",
            "---",
            "---",
            "---",
            "---",
        ]

        # Measure detection performance
        start_time = time.time()
        results = [(text, is_noise_word(text)) for text in receipt_texts]
        end_time = time.time()

        # Performance assertions
        processing_time = end_time - start_time
        assert processing_time < 0.1  # Should process in under 100ms

        # Verify detection accuracy
        noise_count = sum(1 for _, is_noise in results if is_noise)
        total_count = len(results)
        noise_percentage = (noise_count / total_count) * 100

        # Should detect 30-50% as noise in this sample
        assert 30 <= noise_percentage <= 50

        # Verify specific detections
        detections = {text: is_noise for text, is_noise in results}
        assert detections["WALMART"] is False  # Merchant name
        assert detections["$3.49"] is False  # Price
        assert detections["."] is True  # Punctuation
        assert detections["|"] is True  # Separator
        assert detections["---"] is True  # Line separator
        assert detections["****"] is True  # Masked data

    @pytest.mark.integration
    def test_step_function_integration(self, mock_dynamo_client):
        """Test that Step Functions properly handle noise-filtered words."""
        from infra.word_label_step_functions.prepare_embedding_batch_handler import (
            prepare_embedding_batch,
        )

        # Mock receipt words with noise
        words = [
            ReceiptWord(
                receipt_id=1,
                image_id="test-001",
                line_id=1,
                word_id=1,
                text="ITEM",
                is_noise=False,
                embedding_status=EmbeddingStatus.NONE,
            ),
            ReceiptWord(
                receipt_id=1,
                image_id="test-001",
                line_id=1,
                word_id=2,
                text="-",
                is_noise=True,
                embedding_status=EmbeddingStatus.NONE,
            ),
        ]

        mock_dynamo_client.list_receipt_words_by_embedding_status.return_value = (
            words
        )

        with patch(
            "infra.word_label_step_functions.prepare_embedding_batch_handler.get_client_manager"
        ) as mock_get_client:
            mock_client_manager = MagicMock()
            mock_client_manager.dynamo = mock_dynamo_client
            mock_get_client.return_value = mock_client_manager

            # Prepare batch
            batch_data = prepare_embedding_batch()

            # Should only include non-noise word
            assert len(batch_data["words"]) == 1
            assert batch_data["words"][0]["text"] == "ITEM"

    @pytest.mark.integration
    def test_end_to_end_metrics(
        self, mock_dynamo_client, sample_receipt_words
    ):
        """Test end-to-end metrics collection for noise detection."""
        # Track metrics
        metrics = {
            "total_words": 0,
            "noise_words": 0,
            "meaningful_words": 0,
            "embedding_cost_saved": 0.0,
        }

        # Process words
        for word in sample_receipt_words:
            metrics["total_words"] += 1
            if word.is_noise:
                metrics["noise_words"] += 1
                # Approximate cost per embedding
                metrics["embedding_cost_saved"] += 0.0001
            else:
                metrics["meaningful_words"] += 1

        # Verify metrics
        assert metrics["total_words"] == 5
        assert metrics["noise_words"] == 3
        assert metrics["meaningful_words"] == 2
        assert metrics["embedding_cost_saved"] > 0

        # Calculate noise percentage
        noise_percentage = (
            metrics["noise_words"] / metrics["total_words"]
        ) * 100
        assert noise_percentage == 60  # 3/5 = 60%
