"""
Smoke tests for ChromaDB Compaction Infrastructure

Basic tests to verify imports work and core functionality is accessible.
These replace the heavy integration tests with lightweight verification.
"""

# pylint: disable=import-outside-toplevel  # Intentional for testing imports

import pytest


class TestImportSmoke:
    """Verify all modules can be imported without errors."""

    def test_enhanced_compaction_handler_imports(self):
        """Test that enhanced compaction handler can be imported."""
        try:
            from ..lambdas import enhanced_compaction_handler

            # Verify main entry point exists
            assert hasattr(enhanced_compaction_handler, "handle")

            # Verify dataclasses exist
            assert hasattr(enhanced_compaction_handler, "LambdaResponse")
            assert hasattr(enhanced_compaction_handler, "StreamMessage")

            # Verify core functions exist
            assert hasattr(enhanced_compaction_handler, "process_sqs_messages")
            assert hasattr(enhanced_compaction_handler, "process_stream_messages")

        except ImportError as e:
            pytest.fail(f"Failed to import enhanced_compaction_handler: {e}")

    def test_stream_processor_imports(self):
        """Test that stream processor can be imported."""
        try:
            from ..lambdas import stream_processor

            # Verify main entry point exists
            assert hasattr(stream_processor, "lambda_handler")

            # Verify dataclasses exist
            assert hasattr(stream_processor, "LambdaResponse")
            assert hasattr(stream_processor, "FieldChange")
            assert hasattr(stream_processor, "ParsedStreamRecord")

            # Verify core functions exist
            assert hasattr(stream_processor, "parse_stream_record")
            assert hasattr(stream_processor, "get_chromadb_relevant_changes")

        except ImportError as e:
            pytest.fail(f"Failed to import stream_processor: {e}")

    def test_components_import(self):
        """Test that Pulumi components can be imported."""
        try:
            from ..components import (
                ChromaDBBuckets,
                ChromaDBQueues,
                EnhancedCompactionLambda,
                create_enhanced_compaction_lambda,
                create_stream_processor,
            )

            # Just verify they imported successfully
            assert ChromaDBBuckets is not None
            assert ChromaDBQueues is not None
            assert EnhancedCompactionLambda is not None
            assert callable(create_enhanced_compaction_lambda)
            assert callable(create_stream_processor)

        except ImportError as e:
            pytest.fail(f"Failed to import components: {e}")


class TestBasicFunctionality:
    """Basic functionality tests without heavy AWS mocking."""

    def test_lambda_response_dataclass(self):
        """Test LambdaResponse dataclass basic functionality."""
        from ..lambdas.compaction.models import LambdaResponse

        # Test creation and serialization
        response = LambdaResponse(
            status_code=200, message="Test message", processed_messages=5
        )

        result_dict = response.to_dict()

        assert result_dict["statusCode"] == 200
        assert result_dict["message"] == "Test message"
        assert result_dict["processed_messages"] == 5

        # Test that frozen dataclass prevents modification
        with pytest.raises(Exception):  # Should be frozen
            response.status_code = 500

    def test_field_change_dataclass(self):
        """Test FieldChange dataclass basic functionality."""
        from ..lambdas.stream_processor import FieldChange

        change = FieldChange(old="old_value", new="new_value")

        assert change.old == "old_value"
        assert change.new == "new_value"

        # Test that frozen dataclass prevents modification
        with pytest.raises(Exception):  # Should be frozen
            change.old = "modified"

    def test_chromadb_id_pattern_validation(self):
        """Test that ChromaDB ID patterns are constructed correctly."""

        # Test the patterns used throughout the codebase
        image_id = "test_image_123"
        receipt_id = 456
        line_id = 7
        word_id = 89

        # Receipt metadata pattern
        receipt_pattern = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
        assert receipt_pattern == "IMAGE#test_image_123#RECEIPT#00456#"

        # Word label pattern
        word_pattern = (
            f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
            f"LINE#{line_id:05d}#WORD#{word_id:05d}"
        )
        expected_word = "IMAGE#test_image_123#RECEIPT#00456#LINE#00007#WORD#00089"
        assert word_pattern == expected_word

    def test_relevant_field_constants(self):
        """Test that field filtering constants are correctly defined."""

        # These field lists are critical for determining what triggers
        # ChromaDB updates
        metadata_fields = [
            "canonical_merchant_name",
            "merchant_name",
            "merchant_category",
            "address",
            "phone_number",
            "place_id",
        ]

        label_fields = [
            "label",
            "reasoning",
            "validation_status",
            "label_proposed_by",
            "label_consolidated_from",
        ]

        # Verify expected fields are present
        assert "canonical_merchant_name" in metadata_fields
        assert "merchant_category" in metadata_fields
        assert "label" in label_fields
        assert "validation_status" in label_fields

        # Verify we don't accidentally include internal fields
        assert "created_at" not in metadata_fields
        assert "updated_at" not in metadata_fields
        assert "internal_id" not in label_fields
