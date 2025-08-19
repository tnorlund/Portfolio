"""Integration tests for noise word filtering in the embedding pipeline."""

from typing import List
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities import ReceiptWord

from receipt_label.embedding.word.submit import (
    list_receipt_words_with_no_embeddings)


class TestNoiseFilteringIntegration:
    """Test noise word filtering in the receipt_label package."""

    @pytest.fixture
    def sample_receipt_words(self) -> List[ReceiptWord]:
        """Create sample receipt words with mix of noise and meaningful content."""
        return [
            # Meaningful words
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440001",
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
                is_noise=False),
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440001",
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
                is_noise=False),
            # Noise words
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440001",
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
                is_noise=True),
            ReceiptWord(
                receipt_id=1,
                image_id="550e8400-e29b-41d4-a716-446655440001",
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
                is_noise=True),
        ]

    @pytest.mark.integration
    def test_embedding_pipeline_filters_noise(self, sample_receipt_words):
        """Test that the embedding pipeline correctly filters out noise words."""
        mock_client_manager = MagicMock()
        mock_dynamo_client = MagicMock()
        mock_client_manager.dynamo = mock_dynamo_client

        # Mock DynamoDB to return our sample words
        mock_dynamo_client.list_receipt_words_by_embedding_status.return_value = (
            sample_receipt_words
        )

        with patch(
            "receipt_label.embedding.word.submit.get_client_manager"
        ) as mock_get_client:
            mock_get_client.return_value = mock_client_manager

            # Get words for embedding
            words_to_embed = list_receipt_words_with_no_embeddings()

            # Should only include non-noise words
            assert len(words_to_embed) == 2
            assert all(not word.is_noise for word in words_to_embed)
            assert words_to_embed[0].text == "TOTAL"
            assert words_to_embed[1].text == "$15.99"

    @pytest.mark.integration
    def test_backward_compatibility_with_missing_is_noise(self):
        """Test that words without is_noise field are included by default."""
        mock_client_manager = MagicMock()
        mock_dynamo_client = MagicMock()
        mock_client_manager.dynamo = mock_dynamo_client

        # Create words without is_noise field (simulating old data)
        old_word = ReceiptWord(
            receipt_id=1,
            image_id="550e8400-e29b-41d4-a716-446655440001",
            line_id=1,
            word_id=1,
            text="LEGACY",
            bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
            top_left={"x": 0, "y": 0},
            top_right={"x": 100, "y": 0},
            bottom_left={"x": 0, "y": 20},
            bottom_right={"x": 100, "y": 20},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.95,
            embedding_status=EmbeddingStatus.NONE,
            # is_noise not set - should default to False
        )

        mock_dynamo_client.list_receipt_words_by_embedding_status.return_value = [
            old_word
        ]

        with patch(
            "receipt_label.embedding.word.submit.get_client_manager"
        ) as mock_get_client:
            mock_get_client.return_value = mock_client_manager

            # Get words for embedding
            words_to_embed = list_receipt_words_with_no_embeddings()

            # Should include the word (backward compatibility)
            assert len(words_to_embed) == 1
            assert words_to_embed[0].text == "LEGACY"
