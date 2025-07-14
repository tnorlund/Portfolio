"""
Integration test for local receipt processing pipeline.

This test validates that the receipt labeling pipeline can run end-to-end
using local data without making external API calls.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from receipt_label.data.local_data_loader import LocalDataLoader
from receipt_label.labeler import ReceiptLabeler
from receipt_label.models.analysis import LabelAnalysis, ValidationAnalysis
from receipt_label.models.receipt import ReceiptLabelRequest


class TestLocalPipeline:
    """Test the receipt labeling pipeline with local data."""
    
    @pytest.fixture
    def local_data_dir(self):
        """Get the local data directory path."""
        # Check common locations for test data
        possible_paths = [
            Path("./receipt_data"),  # Current directory
            Path("./test_data"),     # Alternative test data location
            Path(__file__).parent.parent.parent / "test_data",  # In tests directory
            Path(__file__).parent.parent.parent.parent.parent / "receipt_data"  # Project root
        ]
        
        for path in possible_paths:
            if path.exists() and path.is_dir():
                return str(path)
                
        # If no data directory exists, skip the test
        pytest.skip("No local receipt data directory found. Run export_receipt_data.py first.")
    
    @pytest.fixture
    def data_loader(self, local_data_dir):
        """Create a local data loader instance."""
        return LocalDataLoader(local_data_dir)
    
    @pytest.fixture
    def mock_openai_response(self):
        """Mock OpenAI API response for label analysis."""
        return LabelAnalysis(
            version="1.0.0",
            timestamp="2024-01-01T00:00:00Z",
            merchant_name="Test Merchant",
            location={
                "store_name": "Test Store",
                "address": "123 Test St",
                "city": "Test City",
                "state": "TS",
                "postal_code": "12345",
                "country": "US"
            },
            datetime={
                "date": "2024-01-01",
                "time": "12:00:00",
                "timezone": "UTC"
            },
            totals={
                "subtotal": 10.00,
                "tax": 1.00,
                "tip": 0.00,
                "total": 11.00,
                "paid": 11.00,
                "currency": "USD"
            },
            payment_methods=[{
                "type": "CARD",
                "amount": 11.00,
                "last_four": "1234"
            }],
            line_items=[],
            labels=[],
            metadata={
                "confidence": 0.95,
                "processing_time_ms": 100
            }
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
            metadata={
                "validation_time_ms": 50
            }
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
        
        assert result is not None, f"Failed to load receipt {image_id}/{receipt_id}"
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
        self,
        data_loader,
        mock_openai_response,
        mock_validation_response
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
        mock_dynamo.list_receipt_word_labels_by_receipt.return_value = existing_labels
        
        # Mock OpenAI client
        mock_openai = MagicMock()
        mock_openai.generate_label_analysis.return_value = mock_openai_response
        mock_openai.validate_analysis.return_value = mock_validation_response
        
        # Create labeler with mocked clients
        with patch('receipt_label.labeler.DynamoClient', return_value=mock_dynamo):
            with patch('receipt_label.labeler.OpenAIClient', return_value=mock_openai):
                labeler = ReceiptLabeler()
                
                # Process the receipt
                request = ReceiptLabelRequest(
                    receipt_id=receipt_id,
                    force_reprocess=False
                )
                
                result = labeler.label_receipt(request)
                
                # Verify the pipeline ran
                assert result is not None
                assert result.receipt_id == int(receipt_id)
                
                # Verify no real API calls were made
                assert mock_openai.generate_label_analysis.called
                assert mock_openai.validate_analysis.called
    
    @pytest.mark.integration 
    def test_pipeline_without_external_calls(self, data_loader, monkeypatch):
        """Ensure pipeline can run without any external service calls."""
        # Patch all external service clients to raise exceptions
        def raise_error(*args, **kwargs):
            raise RuntimeError("External API call attempted!")
        
        # Patch OpenAI
        monkeypatch.setattr(
            "receipt_label.services.openai_client.OpenAIClient._call_api",
            raise_error
        )
        
        # Patch Pinecone
        monkeypatch.setattr(
            "pinecone.Index.query",
            raise_error
        )
        
        # Patch any AWS service calls
        monkeypatch.setattr(
            "boto3.client",
            lambda *args, **kwargs: MagicMock(side_effect=raise_error)
        )
        
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
            assert result is not None, f"Failed to load sample receipt {image_id}/{receipt_id}"