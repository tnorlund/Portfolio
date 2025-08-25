#!/usr/bin/env python3
"""
Update Receipt Word Labels with LangChain Validation Results
==========================================================

This module provides utilities to update ReceiptWordLabel entities with
validation results from the LangChain validation system.

Updates both reasoning and validation_status fields based on ValidationResult.
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import replace
import logging

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
from .validation_tool import ValidationResult

logger = logging.getLogger(__name__)


def update_label_with_validation_result(
    original_label: ReceiptWordLabel,
    validation_result: ValidationResult,
    label_proposed_by: Optional[str] = "langchain_validation"
) -> ReceiptWordLabel:
    """
    Update a ReceiptWordLabel with validation results from LangChain validation.
    
    Args:
        original_label: The original ReceiptWordLabel to update
        validation_result: ValidationResult from LangChain validation
        label_proposed_by: Who/what proposed this validation (for tracking)
        
    Returns:
        Updated ReceiptWordLabel with new reasoning and validation_status
    """
    # Map ValidationResult status to ValidationStatus enum
    status_mapping = {
        "VALID": ValidationStatus.VALID.value,
        "INVALID": ValidationStatus.INVALID.value,
        "NEEDS_REVIEW": ValidationStatus.NEEDS_REVIEW.value,
        "PENDING": ValidationStatus.PENDING.value
    }
    
    new_validation_status = status_mapping.get(
        validation_result.validation_status, 
        ValidationStatus.NEEDS_REVIEW.value
    )
    
    # Create updated label with new validation results
    updated_label = replace(
        original_label,
        reasoning=validation_result.reasoning,
        validation_status=new_validation_status,
        label_proposed_by=label_proposed_by,
        timestamp_added=datetime.now().isoformat()
    )
    
    return updated_label


def update_receipt_labels_batch(
    dynamo_client: DynamoClient,
    validation_results: List[Dict[str, Any]],
    label_proposed_by: Optional[str] = "langchain_validation",
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    Update multiple receipt word labels with validation results in batch.
    
    Args:
        dynamo_client: DynamoDB client for updates
        validation_results: List of dicts with 'label' and 'result' keys
        label_proposed_by: Who/what proposed these validations
        dry_run: If True, don't actually update database
        
    Returns:
        Summary of updates performed
    
    Example validation_results format:
    [
        {
            'label': ReceiptWordLabel(...),
            'result': ValidationResult(validation_status="VALID", reasoning="...")
        },
        ...
    ]
    """
    logger.info("Starting batch update of %d receipt word labels", len(validation_results))
    
    updated_labels = []
    summary = {
        "total_processed": len(validation_results),
        "successful_updates": 0,
        "failed_updates": 0,
        "validation_status_counts": {
            "VALID": 0,
            "INVALID": 0,
            "NEEDS_REVIEW": 0,
            "PENDING": 0
        },
        "errors": []
    }
    
    # Process each validation result
    for i, validation_data in enumerate(validation_results):
        try:
            original_label = validation_data['label']
            validation_result = validation_data['result']
            
            # Update the label with validation results
            updated_label = update_label_with_validation_result(
                original_label,
                validation_result,
                label_proposed_by
            )
            
            updated_labels.append(updated_label)
            
            # Track status counts
            status = validation_result.validation_status
            if status in summary["validation_status_counts"]:
                summary["validation_status_counts"][status] += 1
            
            logger.debug("Updated label %d/%d: %s → %s", 
                        i+1, len(validation_results), 
                        original_label.label, status)
            
        except Exception as e:
            logger.error("Error processing validation result %d: %s", i, e)
            summary["errors"].append({
                "index": i,
                "error": str(e),
                "label_info": getattr(validation_data.get('label'), 'label', 'unknown')
            })
            summary["failed_updates"] += 1
            continue
    
    # Update database if not dry run
    if not dry_run and updated_labels:
        try:
            logger.info("Updating %d labels in database", len(updated_labels))
            dynamo_client.update_receipt_word_labels(updated_labels)
            summary["successful_updates"] = len(updated_labels)
            logger.info("Successfully updated %d receipt word labels", len(updated_labels))
            
        except Exception as e:
            logger.error("Failed to update labels in database: %s", e)
            summary["database_error"] = str(e)
            summary["successful_updates"] = 0
            summary["failed_updates"] = len(updated_labels)
    
    elif dry_run:
        summary["successful_updates"] = len(updated_labels)
        logger.info("Dry run completed - would have updated %d labels", len(updated_labels))
    
    return summary


def validate_and_update_labels(
    dynamo_client: DynamoClient,
    labels: List[ReceiptWordLabel],
    validator,  # StructuredReceiptValidator instance
    context_preparation_service,  # ContextPreparationService instance
    dry_run: bool = False,
    batch_size: int = 10
) -> Dict[str, Any]:
    """
    Complete workflow: validate labels using LangChain and update database.
    
    Args:
        dynamo_client: DynamoDB client
        labels: List of ReceiptWordLabel to validate
        validator: StructuredReceiptValidator instance
        context_preparation_service: ContextPreparationService instance 
        dry_run: If True, don't actually update database
        batch_size: Number of labels to process in each batch
        
    Returns:
        Summary of validation and update process
    """
    logger.info("Starting validation and update workflow for %d labels", len(labels))
    
    # Prepare validation contexts
    try:
        import asyncio
        contexts = asyncio.run(context_preparation_service.prepare_validation_context(labels))
        logger.info("Prepared validation contexts for %d labels", len(contexts))
        
    except Exception as e:
        logger.error("Failed to prepare validation contexts: %s", e)
        return {
            "error": f"Context preparation failed: {e}",
            "total_processed": 0,
            "successful_updates": 0
        }
    
    # Perform validation and collect results
    validation_results = []
    
    for i, context in enumerate(contexts):
        try:
            word_context = context.word_context
            receipt_context = context.receipt_context
            semantic_context = context.semantic_context
            
            # Format context for LLM
            formatted_context = context_preparation_service.format_context_for_llm(context)
            
            # Validate using LangChain
            result = validator.validate_word_label(
                word_text=word_context.target_word.text,
                label=word_context.label_to_validate,
                context_text=formatted_context,
                receipt_metadata=receipt_context.receipt_metadata,
                similar_valid_words=getattr(semantic_context, 'similar_valid_words', []),
                similar_invalid_words=getattr(semantic_context, 'similar_invalid_words', [])
            )
            
            # Find matching original label
            matching_label = None
            for label in labels:
                if (label.image_id == word_context.target_word.image_id and
                    label.receipt_id == word_context.target_word.receipt_id and
                    label.line_id == word_context.target_word.line_id and
                    label.word_id == word_context.target_word.word_id and
                    label.label == word_context.label_to_validate):
                    matching_label = label
                    break
            
            if matching_label:
                validation_results.append({
                    'label': matching_label,
                    'result': result
                })
                logger.debug("Validated %d/%d: %s → %s", 
                           i+1, len(contexts), 
                           word_context.label_to_validate,
                           result.validation_status)
            else:
                logger.warning("Could not find matching label for context %d", i)
                
        except Exception as e:
            logger.error("Error validating context %d: %s", i, e)
            continue
    
    # Update labels with validation results
    if validation_results:
        return update_receipt_labels_batch(
            dynamo_client,
            validation_results,
            label_proposed_by="langchain_validation_workflow",
            dry_run=dry_run
        )
    else:
        return {
            "error": "No validation results to process",
            "total_processed": 0,
            "successful_updates": 0
        }


# Example usage function
def example_usage():
    """Example of how to use the update functions."""
    from receipt_label.langchain_validation import (
        StructuredReceiptValidator,
        ContextPreparationService
    )
    from receipt_dynamo.data._pulumi import load_env
    import os
    
    # Load environment
    pulumi_env = load_env()
    if not pulumi_env:
        raise ValueError("Pulumi environment not available")
    
    # Create clients
    dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
    validator = StructuredReceiptValidator(api_key=os.getenv("OLLAMA_API_KEY"))
    
    # Create ChromaDB client (simplified for example)
    chroma_client = None  # Would create actual client
    context_service = ContextPreparationService(dynamo_client, chroma_client)
    
    # Get some labels to validate
    labels, _ = dynamo_client.get_receipt_word_labels_by_label(
        label="GRAND_TOTAL", 
        limit=5
    )
    
    # Filter to only labels needing validation
    labels_to_validate = [
        label for label in labels 
        if label.validation_status == ValidationStatus.NONE.value
    ]
    
    # Validate and update
    result = validate_and_update_labels(
        dynamo_client=dynamo_client,
        labels=labels_to_validate,
        validator=validator,
        context_preparation_service=context_service,
        dry_run=True  # Set to False to actually update
    )
    
    print(f"Validation completed: {result}")


if __name__ == "__main__":
    example_usage()