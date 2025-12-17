"""Tests for OpenAI realtime embedding functionality."""

from unittest.mock import MagicMock

from receipt_chroma.embedding.openai.realtime import embed_texts


class TestEmbedTexts:
    """Tests for embed_texts function."""

    def test_embed_texts_success(self) -> None:
        """Test successful text embedding."""
        # Mock the OpenAI client
        mock_client = MagicMock()

        # Mock the embedding response
        mock_embedding1 = MagicMock()
        mock_embedding1.embedding = [0.1, 0.2, 0.3]
        mock_embedding2 = MagicMock()
        mock_embedding2.embedding = [0.4, 0.5, 0.6]
        mock_response = MagicMock()
        mock_response.data = [mock_embedding1, mock_embedding2]
        mock_client.embeddings.create.return_value = mock_response

        # Call the function
        result = embed_texts(mock_client, ["text1", "text2"])

        # Verify the result
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_client.embeddings.create.assert_called_once()

    def test_embed_texts_empty_input(self) -> None:
        """Test embedding with empty input."""
        mock_client = MagicMock()
        result = embed_texts(mock_client, [])
        assert result == []
        mock_client.embeddings.create.assert_not_called()

    def test_embed_texts_with_model_parameter(self) -> None:
        """Test embedding with custom model."""
        mock_client = MagicMock()

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.7, 0.8, 0.9]
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embeddings.create.return_value = mock_response

        result = embed_texts(
            mock_client,
            ["test text"],
            model="text-embedding-ada-002",
        )

        assert result == [[0.7, 0.8, 0.9]]
