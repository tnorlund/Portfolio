import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from concurrent.futures import TimeoutError as FutureTimeoutError

from validate_single_receipt_handler import (
    run_agent_with_retries,
    extract_agent_results,
    sanitize_string,
    validate_handler,
    AGENT_TIMEOUT_SECONDS,
    MAX_AGENT_ATTEMPTS
)


class TestSanitizeString:
    """Test cases for the sanitize_string function."""
    
    def test_sanitize_basic_quotes(self):
        """Test basic quote removal."""
        assert sanitize_string('"hello"') == "hello"
        assert sanitize_string("'hello'") == "hello"
        assert sanitize_string('hello') == "hello"
    
    def test_sanitize_nested_quotes(self):
        """Test handling of nested quotes."""
        assert sanitize_string('"hello "world""') == 'hello "world"'
        assert sanitize_string("'hello 'world''") == "hello 'world'"
    
    def test_sanitize_json_encoded(self):
        """Test handling of JSON-encoded strings."""
        assert sanitize_string('"hello world"') == "hello world"
        assert sanitize_string(json.dumps("hello world")) == "hello world"
    
    def test_sanitize_malformed_input(self):
        """Test handling of malformed input."""
        assert sanitize_string('"unclosed quote') == '"unclosed quote'
        assert sanitize_string('mismatched"') == 'mismatched"'
        assert sanitize_string('') == ''
        assert sanitize_string(None) == ''
        assert sanitize_string(123) == '123'
    
    def test_sanitize_whitespace(self):
        """Test whitespace handling."""
        assert sanitize_string('  hello  ') == 'hello'
        assert sanitize_string('"  hello  "') == 'hello'
        assert sanitize_string('\n\thello\n\t') == 'hello'


class TestExtractAgentResults:
    """Test cases for the extract_agent_results function."""
    
    def test_extract_metadata_success(self):
        """Test successful metadata extraction."""
        # Mock run result with tool_return_metadata
        mock_item = Mock()
        mock_item.raw_item = Mock(
            name="tool_return_metadata",
            arguments=json.dumps({
                "place_id": "test_place",
                "merchant_name": "Test Merchant",
                "address": "123 Test St",
                "phone_number": "555-1234",
                "merchant_category": "Restaurant",
                "matched_fields": ["name", "phone"],
                "validated_by": "phone_lookup",
                "reasoning": "Found by phone"
            })
        )
        
        mock_result = Mock(new_items=[mock_item])
        
        metadata, partial = extract_agent_results(mock_result, 1)
        
        assert metadata is not None
        assert metadata["place_id"] == "test_place"
        assert metadata["merchant_name"] == "Test Merchant"
        assert len(partial) == 0
    
    def test_extract_metadata_json_error(self):
        """Test metadata extraction with JSON parse error."""
        # Mock run result with invalid JSON
        mock_item = Mock()
        mock_item.raw_item = Mock(
            name="tool_return_metadata",
            arguments="invalid json"
        )
        mock_item.output = {"fallback": "data"}
        
        mock_result = Mock(new_items=[mock_item])
        
        metadata, partial = extract_agent_results(mock_result, 1)
        
        assert metadata == {"fallback": "data"}
    
    def test_extract_partial_results(self):
        """Test extraction of partial results from other tools."""
        # Mock run result with multiple tool calls
        mock_phone = Mock()
        mock_phone.raw_item = Mock(name="tool_search_by_phone")
        mock_phone.output = {"result": "phone_data"}
        
        mock_address = Mock()
        mock_address.raw_item = Mock(name="tool_search_by_address")
        mock_address.output = {"result": "address_data"}
        
        mock_other = Mock()
        mock_other.raw_item = Mock(name="other_tool")
        mock_other.output = {"result": "other_data"}
        
        mock_result = Mock(new_items=[mock_phone, mock_address, mock_other])
        
        metadata, partial = extract_agent_results(mock_result, 1)
        
        assert metadata is None
        assert len(partial) == 2
        assert partial[0]["function"] == "tool_search_by_phone"
        assert partial[1]["function"] == "tool_search_by_address"


class TestRunAgentWithRetries:
    """Test cases for the run_agent_with_retries function."""
    
    @patch('validate_single_receipt_handler.Runner')
    @patch('validate_single_receipt_handler.extract_agent_results')
    def test_successful_first_attempt(self, mock_extract, mock_runner):
        """Test successful agent run on first attempt."""
        # Setup mocks
        mock_extract.return_value = ({"test": "metadata"}, [])
        mock_runner.run_sync.return_value = Mock()
        
        agent = Mock()
        user_input = {"image_id": "123", "receipt_id": 1}
        
        metadata, partial = run_agent_with_retries(agent, user_input)
        
        assert metadata == {"test": "metadata"}
        assert len(partial) == 0
        assert mock_runner.run_sync.call_count == 1
    
    @patch('validate_single_receipt_handler.Runner')
    @patch('validate_single_receipt_handler.extract_agent_results')
    def test_retry_on_failure(self, mock_extract, mock_runner):
        """Test retry logic when first attempt fails."""
        # First attempt returns None, second succeeds
        mock_extract.side_effect = [
            (None, [{"function": "partial1"}]),
            ({"test": "metadata"}, [{"function": "partial2"}])
        ]
        mock_runner.run_sync.return_value = Mock()
        
        agent = Mock()
        user_input = {"image_id": "123", "receipt_id": 1}
        
        metadata, partial = run_agent_with_retries(agent, user_input, max_attempts=2)
        
        assert metadata == {"test": "metadata"}
        assert len(partial) == 2
        assert mock_runner.run_sync.call_count == 2
    
    @patch('validate_single_receipt_handler.ThreadPoolExecutor')
    @patch('validate_single_receipt_handler.extract_agent_results')
    def test_timeout_handling(self, mock_extract, mock_executor_class):
        """Test timeout handling during agent execution."""
        # Mock the executor and future
        mock_future = Mock()
        mock_future.result.side_effect = FutureTimeoutError()
        mock_future.cancel.return_value = True
        
        mock_executor = Mock()
        mock_executor.__enter__ = Mock(return_value=mock_executor)
        mock_executor.__exit__ = Mock(return_value=None)
        mock_executor.submit.return_value = mock_future
        
        mock_executor_class.return_value = mock_executor
        
        agent = Mock()
        user_input = {"image_id": "123", "receipt_id": 1}
        
        metadata, partial = run_agent_with_retries(agent, user_input, max_attempts=1)
        
        assert metadata is None
        assert len(partial) == 0
        assert mock_future.cancel.called
    
    @patch('validate_single_receipt_handler.Runner')
    @patch('validate_single_receipt_handler.extract_agent_results')
    def test_all_attempts_fail(self, mock_extract, mock_runner):
        """Test when all agent attempts fail."""
        # All attempts return None
        mock_extract.return_value = (None, [{"function": "partial"}])
        mock_runner.run_sync.return_value = Mock()
        
        agent = Mock()
        user_input = {"image_id": "123", "receipt_id": 1}
        
        metadata, partial = run_agent_with_retries(agent, user_input, max_attempts=3)
        
        assert metadata is None
        assert len(partial) == 3  # Partial results from all attempts
        assert mock_runner.run_sync.call_count == 3


class TestValidateHandler:
    """Test cases for the main validate_handler function."""
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.run_agent_with_retries')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.build_receipt_metadata_from_result_no_match')
    def test_handler_agent_failure(self, mock_build_no_match, mock_write_dynamo, 
                                   mock_run_agent, mock_get_details):
        """Test handler when agent fails to return metadata."""
        # Setup mocks
        mock_get_details.return_value = (
            Mock(),  # receipt
            [Mock(text="line1"), Mock(text="line2")],  # receipt_lines
            [],  # receipt_words
            [],  # receipt_letters
            [],  # receipt_word_tags
            []   # receipt_word_labels
        )
        
        mock_run_agent.return_value = (None, [])  # Agent fails
        
        mock_no_match_meta = Mock()
        mock_no_match_meta.reasoning = "Original reasoning"
        mock_build_no_match.return_value = mock_no_match_meta
        
        event = {"image_id": "123", "receipt_id": 1}
        
        result = validate_handler(event, None)
        
        assert result["status"] == "no_match"
        assert result["failure_reason"] == "agent_attempts_exhausted"
        assert "Agent validation failed" in mock_no_match_meta.reasoning
        assert mock_write_dynamo.called
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.run_agent_with_retries')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.ReceiptMetadata')
    def test_handler_success(self, mock_receipt_metadata_class, mock_write_dynamo, 
                           mock_run_agent, mock_get_details):
        """Test successful handler execution."""
        # Setup mocks
        mock_get_details.return_value = (
            Mock(),  # receipt
            [Mock(text="line1")],  # receipt_lines
            [],  # receipt_words
            [],  # receipt_letters
            [],  # receipt_word_tags
            []   # receipt_word_labels
        )
        
        mock_run_agent.return_value = ({
            "place_id": "test_place",
            "merchant_name": "Test Merchant",
            "address": "123 Test St",
            "phone_number": "555-1234",
            "merchant_category": "Restaurant",
            "matched_fields": ["name", "phone"],
            "validated_by": "phone_lookup",
            "reasoning": "Found by phone"
        }, [])
        
        event = {"image_id": "123", "receipt_id": 1}
        
        result = validate_handler(event, None)
        
        assert result["status"] == "processed"
        assert result["place_id"] == "test_place"
        assert result["merchant_name"] == "Test Merchant"
        assert mock_receipt_metadata_class.called
        assert mock_write_dynamo.called
    
    @patch('validate_single_receipt_handler.get_receipt_details')
    @patch('validate_single_receipt_handler.run_agent_with_retries')
    @patch('validate_single_receipt_handler.write_receipt_metadata_to_dynamo')
    @patch('validate_single_receipt_handler.ReceiptMetadata')
    def test_handler_with_malformed_metadata(self, mock_receipt_metadata_class, 
                                           mock_write_dynamo, mock_run_agent, mock_get_details):
        """Test handler with malformed metadata requiring sanitization."""
        # Setup mocks
        mock_get_details.return_value = (
            Mock(),  # receipt
            [Mock(text="line1")],  # receipt_lines
            [],  # receipt_words
            [],  # receipt_letters
            [],  # receipt_word_tags
            []   # receipt_word_labels
        )
        
        # Metadata with quotes that need sanitization
        mock_run_agent.return_value = ({
            "place_id": '"test_place"',
            "merchant_name": '"Test "Merchant""',
            "address": "'123 Test St'",
            "phone_number": "555-1234",
            "merchant_category": '""Restaurant""',
            "matched_fields": ["name", "phone"],
            "validated_by": "phone_lookup",
            "reasoning": '"Found by phone"'
        }, [])
        
        event = {"image_id": "123", "receipt_id": 1}
        
        result = validate_handler(event, None)
        
        # Check that sanitization was applied
        assert result["place_id"] == "test_place"
        assert result["merchant_name"] == 'Test "Merchant"'
        
        # Verify ReceiptMetadata was called with sanitized values
        call_args = mock_receipt_metadata_class.call_args[1]
        assert call_args["place_id"] == "test_place"
        assert call_args["merchant_name"] == 'Test "Merchant"'
        assert call_args["address"] == "123 Test St"
        assert call_args["merchant_category"] == "Restaurant"
        assert call_args["reasoning"] == "Found by phone"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])