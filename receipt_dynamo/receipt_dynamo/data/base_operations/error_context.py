"""
Error context extraction utilities for DynamoDB operations.

This module provides utilities to extract contextual information
from operation arguments for generating meaningful error messages.
"""

from typing import Any, Dict, Optional


class ErrorContextExtractor:
    """Extracts context information from operation arguments for error messages."""
    
    @staticmethod
    def extract_entity_context(context: Optional[Dict[str, Any]]) -> str:
        """Extract entity information from context for error messages."""
        if not context or "args" not in context:
            return "unknown entity"

        args = context["args"]
        if not args:
            return "unknown entity"
            
        # Check if it's a list (for batch operations)
        if isinstance(args[0], list):
            return "list"
            
        if not hasattr(args[0], "__class__"):
            return "unknown entity"
            
        entity = args[0]
        class_name = entity.__class__.__name__
        
        # Extract specific identifiers for better error messages
        if hasattr(entity, "job_id"):
            return f"{class_name} with job_id={entity.job_id}"
        elif hasattr(entity, "receipt_id"):
            return f"{class_name} with receipt_id={entity.receipt_id}"
        elif hasattr(entity, "image_id"):
            return f"{class_name} with image_id={entity.image_id}"
        elif hasattr(entity, "queue_name"):
            return f"{class_name} with queue_name={entity.queue_name}"
        elif hasattr(entity, "instance_id"):
            return f"{class_name} with instance_id={entity.instance_id}"
            
        return class_name

    @staticmethod
    def extract_entity_type(operation: str) -> str:
        """Extract entity type from operation name."""
        # Common entity patterns in operation names
        # IMPORTANT: Order matters - longer patterns must come before shorter ones
        entity_patterns = [
            "receipt_chatgpt_validation", "receipt_chat_gpt_validation",  # Both variants
            "receipt_line_item_analysis", "receipt_line_item_analyses",
            "receipt_label_analysis", "receipt_label_analyses",
            "receipt_validation_categories", "receipt_validation_category",  # plural first
            "receipt_validation_result", "receipt_validation_summary", 
            "receipt_structure_analysis", "receipt_structure_analyses",
            "receipt_letter", "receipt_field", "receipt_word_label", 
            "receipt_word", "job_checkpoint", "job_dependency", "job_log", "queue_job", "ocr_job", 
            "places_cache", "receipt", "queue", "image", "job", "word", "letter", "instance"
        ]
        
        # Special case handling for specific operations
        if "job_from_queue" in operation or "job_to_queue" in operation:
            return "queue_job"
            
        for pattern in entity_patterns:
            if pattern in operation:
                # Special handling for ChatGPT
                if "chat_gpt" in pattern or "chatgpt" in pattern:
                    # Check if it's a plural operation
                    if "validations" in operation:
                        return "receipt ChatGPT validations"
                    return "receipt ChatGPT validation"
                # Handle plural forms like "categories" -> "category"
                if pattern.endswith("ies"):
                    # Convert "categories" to "category" for the error message
                    singular = pattern[:-3] + "y"
                    return singular.replace("_", " ")
                return pattern.replace("_", " ")
            # Also check for plural forms (e.g., "analyses" instead of "analysis")
            if pattern.endswith("analysis") and pattern[:-8] + "analyses" in operation:
                return pattern.replace("_", " ")
                
        return "entity"

    @staticmethod
    def normalize_entity_name(entity_name: str) -> str:
        """
        Normalize entity name for consistent display in error messages.
        
        Converts CamelCase to lowercase with spaces for human-friendly messages.
        Examples:
            ReceiptLineItemAnalysis -> receipt line item analysis
            JobCheckpoint -> job checkpoint
        """
        if not entity_name:
            return "entity"
            
        # Handle already normalized names
        if " " in entity_name or entity_name.islower():
            return entity_name
            
        # Convert CamelCase to lowercase with spaces
        result = []
        for i, char in enumerate(entity_name):
            if i > 0 and char.isupper() and entity_name[i-1].islower():
                result.append(" ")
            result.append(char.lower())
            
        return "".join(result)

    @staticmethod
    def extract_operation_type(operation: str) -> str:
        """Extract the operation type (add, get, update, delete, list) from operation name."""
        operation_lower = operation.lower()
        
        # Check common operation prefixes
        for op_type in ["add", "get", "update", "delete", "list"]:
            if operation_lower.startswith(op_type):
                return op_type
                
        # Check if operation contains the type
        for op_type in ["add", "get", "update", "delete", "list"]:
            if op_type in operation_lower:
                return op_type
                
        return "process"  # Default fallback