"""
Integration tests for the validate_single_receipt_handler.
These tests exercise the full agent-based validation flow.
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

from validate_single_receipt_handler import (
    validate_handler,
    agent,
    TOOLS
)


@pytest.fixture
def mock_receipt_data():
    """Fixture providing mock receipt data."""
    return {
        "receipt": Mock(),
        "receipt_lines": [
            Mock(text="STARBUCKS"),
            Mock(text="123 MAIN ST"),
            Mock(text="ANYTOWN, CA 12345"),
            Mock(text="(555) 123-4567")
        ],
        "receipt_words": [
            Mock(text="STARBUCKS", tag="merchant_name"),
            Mock(text="123", tag="address"),
            Mock(text="MAIN", tag="address"),
            Mock(text="ST", tag="address"),
            Mock(text="555", tag="phone"),
            Mock(text="123", tag="phone"),
            Mock(text="4567", tag="phone")
        ],
        "receipt_letters": [],
        "receipt_word_tags": [],
        "receipt_word_labels": []
    }


@pytest.fixture
def mock_google_places_result():
    """Fixture providing mock Google Places API result."""
    return {
        "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "name": "Starbucks",
        "formatted_address": "123 Main St, Anytown, CA 12345, USA",
        "formatted_phone_number": "(555) 123-4567",
        "types": ["cafe", "restaurant", "food", "point_of_interest"]
    }


class TestAgentIntegration:
    """Integration tests for the agent-based validation flow."""
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.PlacesAPI')
    def test_successful_phone_lookup_flow(self, mock_places_api_class, 
                                        mock_write_dynamo, mock_get_details, 
                                        mock_receipt_data, mock_google_places_result):
        """Test successful validation via phone lookup."""
        # Setup receipt data
        mock_get_details.return_value = (
            mock_receipt_data["receipt"],
            mock_receipt_data["receipt_lines"],
            mock_receipt_data["receipt_words"],
            mock_receipt_data["receipt_letters"],
            mock_receipt_data["receipt_word_tags"],
            mock_receipt_data["receipt_word_labels"]
        )
        
        # Mock PlacesAPI to return successful phone lookup
        mock_places_api = Mock()
        mock_places_api.search_by_phone.return_value = mock_google_places_result
        mock_places_api_class.return_value = mock_places_api
        
        # Mock the agent to use real tools but with mocked API
        with patch('validate_single_receipt_handler.tool_search_by_phone') as mock_phone_tool:
            mock_phone_tool.return_value = mock_google_places_result
            
            event = {"image_id": "test-123", "receipt_id": 1}
            
            # Run the handler (this will use the real agent)
            result = validate_handler(event, None)
            
            # Verify successful processing
            assert result["status"] == "processed"
            assert "place_id" in result
            assert "merchant_name" in result
            
            # Verify DynamoDB write was called
            assert mock_write_dynamo.called
            
            # Check the ReceiptMetadata that was written
            written_metadata = mock_write_dynamo.call_args[0][0]
            assert written_metadata.place_id == mock_google_places_result["place_id"]
            assert written_metadata.validated_by == "phone_lookup"
            assert len(written_metadata.matched_fields) > 0
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.PlacesAPI')
    def test_fallback_to_address_lookup(self, mock_places_api_class, 
                                      mock_write_dynamo, mock_get_details, 
                                      mock_receipt_data, mock_google_places_result):
        """Test fallback to address lookup when phone lookup fails."""
        # Setup receipt data
        mock_get_details.return_value = (
            mock_receipt_data["receipt"],
            mock_receipt_data["receipt_lines"],
            mock_receipt_data["receipt_words"],
            mock_receipt_data["receipt_letters"],
            mock_receipt_data["receipt_word_tags"],
            mock_receipt_data["receipt_word_labels"]
        )
        
        # Mock PlacesAPI - phone fails, address succeeds
        mock_places_api = Mock()
        mock_places_api.search_by_phone.return_value = {}  # Empty result
        mock_places_api.search_by_address.return_value = {
            "geometry": {"location": {"lat": 37.7749, "lng": -122.4194}},
            "place_id": "address_lookup_place_id"
        }
        mock_places_api.search_nearby.return_value = [mock_google_places_result]
        mock_places_api_class.return_value = mock_places_api
        
        event = {"image_id": "test-456", "receipt_id": 2}
        
        # Run the handler
        result = validate_handler(event, None)
        
        # Verify processing (may succeed or fall back to no_match)
        assert result["status"] in ["processed", "no_match"]
        assert mock_write_dynamo.called
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.build_receipt_metadata_from_result_no_match')
    @patch('validate_single_receipt_handler.Runner')
    def test_agent_complete_failure(self, mock_runner, mock_build_no_match, 
                                  mock_write_dynamo, mock_get_details, 
                                  mock_receipt_data):
        """Test complete agent failure leading to no-match fallback."""
        # Setup receipt data
        mock_get_details.return_value = (
            mock_receipt_data["receipt"],
            mock_receipt_data["receipt_lines"],
            mock_receipt_data["receipt_words"],
            mock_receipt_data["receipt_letters"],
            mock_receipt_data["receipt_word_tags"],
            mock_receipt_data["receipt_word_labels"]
        )
        
        # Mock agent to fail consistently
        mock_runner.run_sync.side_effect = Exception("Agent error")
        
        # Mock no-match builder
        mock_no_match_meta = Mock()
        mock_no_match_meta.reasoning = "No merchant found"
        mock_build_no_match.return_value = mock_no_match_meta
        
        event = {"image_id": "test-789", "receipt_id": 3}
        
        # Run the handler
        result = validate_handler(event, None)
        
        # Verify no-match result
        assert result["status"] == "no_match"
        assert result["failure_reason"] == "agent_attempts_exhausted"
        assert mock_write_dynamo.called
        assert "Agent validation failed" in mock_no_match_meta.reasoning
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    def test_text_search_validation_flow(self, mock_write_dynamo, mock_get_details, 
                                       mock_receipt_data):
        """Test validation via text search when phone and address fail."""
        # Setup receipt data with no phone number
        receipt_data_no_phone = mock_receipt_data.copy()
        receipt_data_no_phone["receipt_lines"] = [
            Mock(text="UNIQUE COFFEE SHOP"),
            Mock(text="789 SPECIAL AVE"),
            Mock(text="SOMEWHERE, NY 10001")
        ]
        
        mock_get_details.return_value = (
            receipt_data_no_phone["receipt"],
            receipt_data_no_phone["receipt_lines"],
            [],  # No tagged words
            receipt_data_no_phone["receipt_letters"],
            receipt_data_no_phone["receipt_word_tags"],
            receipt_data_no_phone["receipt_word_labels"]
        )
        
        # Mock text search to succeed
        with patch('validate_single_receipt_handler.tool_search_by_text') as mock_text_tool:
            mock_text_tool.return_value = {
                "place_id": "text_search_place_id",
                "name": "Unique Coffee Shop",
                "formatted_address": "789 Special Ave, Somewhere, NY 10001",
                "types": ["cafe"]
            }
            
            event = {"image_id": "test-text", "receipt_id": 4}
            
            # Run the handler
            result = validate_handler(event, None)
            
            # Verify result (depends on agent logic)
            assert result["status"] in ["processed", "no_match"]
            assert mock_write_dynamo.called


class TestEndToEndScenarios:
    """End-to-end test scenarios covering real-world cases."""
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    def test_receipt_with_partial_data(self, mock_write_dynamo, mock_get_details):
        """Test handling receipt with partial/incomplete data."""
        # Receipt with only merchant name, no phone or address
        mock_get_details.return_value = (
            Mock(),
            [Mock(text="WALMART"), Mock(text="Thank you for shopping")],
            [Mock(text="WALMART", tag="merchant_name")],
            [],
            [],
            []
        )
        
        event = {"image_id": "partial-123", "receipt_id": 5}
        
        result = validate_handler(event, None)
        
        # Should process but may have limited matching
        assert result["status"] in ["processed", "no_match"]
        assert mock_write_dynamo.called
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.ThreadPoolExecutor')
    def test_agent_timeout_recovery(self, mock_executor_class, mock_write_dynamo, 
                                  mock_get_details, mock_receipt_data):
        """Test recovery from agent timeout."""
        # Setup receipt data
        mock_get_details.return_value = (
            mock_receipt_data["receipt"],
            mock_receipt_data["receipt_lines"],
            mock_receipt_data["receipt_words"],
            mock_receipt_data["receipt_letters"],
            mock_receipt_data["receipt_word_tags"],
            mock_receipt_data["receipt_word_labels"]
        )
        
        # Mock timeout on first attempt, success on second
        mock_future1 = Mock()
        mock_future1.result.side_effect = TimeoutError()
        mock_future1.cancel.return_value = True
        
        mock_future2 = Mock()
        mock_result = Mock()
        mock_result.new_items = []
        mock_future2.result.return_value = mock_result
        
        mock_executor = Mock()
        mock_executor.__enter__ = Mock(return_value=mock_executor)
        mock_executor.__exit__ = Mock(return_value=None)
        mock_executor.submit.side_effect = [mock_future1, mock_future2]
        
        mock_executor_class.return_value = mock_executor
        
        event = {"image_id": "timeout-test", "receipt_id": 6}
        
        result = validate_handler(event, None)
        
        # Should handle timeout and retry
        assert result["status"] in ["processed", "no_match"]
        assert mock_write_dynamo.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])