"""Tests for merchant validation data access module."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from botocore.exceptions import ClientError
from receipt_label.merchant_validation.data_access import (
    get_receipt_details,
    list_all_receipt_metadatas,
    list_receipt_metadatas,
    list_receipts_for_merchant_validation,
    persist_alias_updates,
    query_records_by_place_id,
    write_receipt_metadata_to_dynamo,
)

from receipt_dynamo.entities import Receipt, ReceiptMetadata, ReceiptWord


class TestListReceiptMetadatas:
    """Tests for list_receipt_metadatas function."""

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_list_receipt_metadatas_success(self, mock_get_client_manager):
        """Test successful listing of receipt metadatas."""
        # Arrange
        mock_metadatas = [
            Mock(spec=ReceiptMetadata),
            Mock(spec=ReceiptMetadata),
        ]
        mock_client_manager = Mock()
        mock_client_manager.dynamo.listReceiptMetadatas.return_value = (
            mock_metadatas,
            None,
        )
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        result = list_receipt_metadatas()

        # Assert
        assert result == mock_metadatas
        mock_client_manager.dynamo.listReceiptMetadatas.assert_called_once()

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_list_receipt_metadatas_empty_result(
        self, mock_get_client_manager
    ):
        """Test handling of empty result from DynamoDB."""
        # Arrange
        mock_client_manager = Mock()
        mock_client_manager.dynamo.listReceiptMetadatas.return_value = None
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        result = list_receipt_metadatas()

        # Assert
        assert result == []

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_list_receipt_metadatas_client_error(
        self, mock_get_client_manager
    ):
        """Test handling of DynamoDB client errors."""
        # Arrange
        mock_client_manager = Mock()
        mock_client_manager.dynamo.listReceiptMetadatas.side_effect = (
            ClientError(
                {"Error": {"Code": "ValidationException"}},
                "listReceiptMetadatas",
            )
        )
        mock_get_client_manager.return_value = mock_client_manager

        # Act & Assert
        with pytest.raises(ClientError):
            list_receipt_metadatas()


class TestGetReceiptDetails:
    """Tests for get_receipt_details function."""

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_get_receipt_details_success(self, mock_get_client_manager):
        """Test successful retrieval of receipt details."""
        # Arrange
        image_id = "IMG123"
        receipt_id = 1

        mock_receipt = Mock(spec=Receipt)
        mock_lines = [Mock()]
        mock_words = [Mock(spec=ReceiptWord)]
        mock_letters = [Mock()]
        mock_labels = [Mock()]

        mock_client_manager = Mock()
        mock_dynamo = mock_client_manager.dynamo
        mock_dynamo.getReceipt.return_value = mock_receipt
        mock_dynamo.getReceiptLines.return_value = mock_lines
        mock_dynamo.getReceiptWords.return_value = mock_words
        mock_dynamo.getReceiptLetters.return_value = mock_letters
        mock_dynamo.getReceiptWordLabels.return_value = mock_labels
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        result = get_receipt_details(image_id, receipt_id)

        # Assert
        assert result == (
            mock_receipt,
            mock_lines,
            mock_words,
            mock_letters,
            mock_labels,
        )
        mock_dynamo.getReceipt.assert_called_once_with(image_id, receipt_id)
        mock_dynamo.getReceiptLines.assert_called_once_with(
            image_id, receipt_id
        )
        mock_dynamo.getReceiptWords.assert_called_once_with(
            image_id, receipt_id
        )
        mock_dynamo.getReceiptLetters.assert_called_once_with(
            image_id, receipt_id
        )
        mock_dynamo.getReceiptWordLabels.assert_called_once_with(
            image_id, receipt_id
        )

    def test_get_receipt_details_invalid_image_id(self):
        """Test validation of invalid image_id."""
        with pytest.raises(ValueError, match="Invalid image_id"):
            get_receipt_details("", 1)

        with pytest.raises(ValueError, match="Invalid image_id"):
            get_receipt_details(None, 1)

    def test_get_receipt_details_invalid_receipt_id(self):
        """Test validation of invalid receipt_id."""
        with pytest.raises(ValueError, match="Invalid receipt_id"):
            get_receipt_details("IMG123", -1)

        with pytest.raises(ValueError, match="Invalid receipt_id"):
            get_receipt_details("IMG123", "not_an_int")


class TestWriteReceiptMetadataToDynamo:
    """Tests for write_receipt_metadata_to_dynamo function."""

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_write_receipt_metadata_success(self, mock_get_client_manager):
        """Test successful writing of receipt metadata."""
        # Arrange
        metadata = Mock(spec=ReceiptMetadata)
        metadata.image_id = "IMG123"
        metadata.receipt_id = 1

        mock_client_manager = Mock()
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        write_receipt_metadata_to_dynamo(metadata)

        # Assert
        mock_client_manager.dynamo.addReceiptMetadata.assert_called_once_with(
            metadata
        )

    def test_write_receipt_metadata_none_metadata(self):
        """Test validation of None metadata."""
        with pytest.raises(ValueError, match="Metadata cannot be None"):
            write_receipt_metadata_to_dynamo(None)

    def test_write_receipt_metadata_invalid_image_id(self):
        """Test validation of metadata with invalid image_id."""
        metadata = Mock()
        metadata.image_id = ""
        metadata.receipt_id = 1

        with pytest.raises(ValueError, match="valid image_id"):
            write_receipt_metadata_to_dynamo(metadata)

    def test_write_receipt_metadata_invalid_receipt_id(self):
        """Test validation of metadata with invalid receipt_id."""
        metadata = Mock()
        metadata.image_id = "IMG123"
        metadata.receipt_id = None

        with pytest.raises(ValueError, match="valid receipt_id"):
            write_receipt_metadata_to_dynamo(metadata)


class TestQueryRecordsByPlaceId:
    """Tests for query_records_by_place_id function."""

    @patch(
        "receipt_label.merchant_validation.data_access.list_receipt_metadatas"
    )
    def test_query_records_by_place_id_success(self, mock_list):
        """Test successful querying by place_id."""
        # Arrange
        place_id = "ChIJN1t_tDeuEmsRUsoyG83frY4"

        metadata1 = Mock(spec=ReceiptMetadata)
        metadata1.place_id = place_id
        metadata2 = Mock(spec=ReceiptMetadata)
        metadata2.place_id = "different_place_id"
        metadata3 = Mock(spec=ReceiptMetadata)
        metadata3.place_id = place_id

        mock_list.return_value = [metadata1, metadata2, metadata3]

        # Act
        result = query_records_by_place_id(place_id)

        # Assert
        assert len(result) == 2
        assert result == [metadata1, metadata3]

    def test_query_records_by_place_id_invalid_place_id(self):
        """Test validation of invalid place_id."""
        with pytest.raises(ValueError, match="Invalid place_id"):
            query_records_by_place_id("")

        with pytest.raises(ValueError, match="Invalid place_id"):
            query_records_by_place_id(None)


class TestPersistAliasUpdates:
    """Tests for persist_alias_updates function."""

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_persist_alias_updates_success(self, mock_get_client_manager):
        """Test successful persistence of alias updates."""
        # Arrange
        records = [Mock(spec=ReceiptMetadata) for _ in range(3)]

        mock_client_manager = Mock()
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        persist_alias_updates(records)

        # Assert
        assert mock_client_manager.dynamo.updateReceiptMetadata.call_count == 3
        for record in records:
            mock_client_manager.dynamo.updateReceiptMetadata.assert_any_call(
                record
            )

    @patch("receipt_label.merchant_validation.data_access.get_client_manager")
    def test_persist_alias_updates_large_batch(self, mock_get_client_manager):
        """Test persistence with large batch (more than 25 records)."""
        # Arrange
        records = [Mock(spec=ReceiptMetadata) for _ in range(50)]

        mock_client_manager = Mock()
        mock_get_client_manager.return_value = mock_client_manager

        # Act
        persist_alias_updates(records)

        # Assert
        assert (
            mock_client_manager.dynamo.updateReceiptMetadata.call_count == 50
        )

    def test_persist_alias_updates_empty_list(self):
        """Test handling of empty records list."""
        # Act (should not raise an exception)
        persist_alias_updates([])

    def test_persist_alias_updates_invalid_input(self):
        """Test validation of invalid input."""
        with pytest.raises(ValueError, match="Records must be a list"):
            persist_alias_updates("not_a_list")
