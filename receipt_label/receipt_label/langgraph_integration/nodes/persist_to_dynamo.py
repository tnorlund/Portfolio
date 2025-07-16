"""Persist validated labels to DynamoDB node.

This node writes the final validated labels to DynamoDB using ReceiptWordLabel entities,
following the validation pipeline pattern from infra/validation_pipeline/lambda.py.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from langchain_core.tools import tool

from ...utils.client_manager import get_client_manager
from ..state import ReceiptProcessingState

logger = logging.getLogger(__name__)


@tool
async def create_receipt_word_label_entities(
    validated_labels: Dict[int, Dict],
    receipt_id: int,
    image_id: str,
    receipt_words: List[Dict]
) -> List[Dict]:
    """Create ReceiptWordLabel entities from validated labels.
    
    Args:
        validated_labels: Dict mapping word_id to label info
        receipt_id: Receipt identifier
        image_id: Image UUID
        receipt_words: List of all receipt words for line_id lookup
        
    Returns:
        List of ReceiptWordLabel entity dictionaries
    """
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
    
    # Create word_id to line_id mapping
    word_to_line = {w["word_id"]: w["line_id"] for w in receipt_words}
    
    label_entities = []
    
    for word_id, label_info in validated_labels.items():
        if word_id not in word_to_line:
            logger.warning(f"Word {word_id} not found in receipt_words, skipping")
            continue
        
        # Determine validation status
        validation_status = label_info.get("validation_status", "VALID")
        if validation_status == "corrected":
            validation_status = "VALID"  # Corrected labels are now valid
        elif validation_status == "needs_review":
            validation_status = "PENDING"  # Needs human review
        else:
            validation_status = "VALID"  # Default for validated labels
        
        # Create ReceiptWordLabel entity
        try:
            label_entity = ReceiptWordLabel(
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=word_to_line[word_id],
                word_id=word_id,
                label=label_info["label"],
                reasoning=label_info.get("reasoning", "Second pass validation"),
                timestamp_added=datetime.now(),
                validation_status=validation_status,
                label_proposed_by=label_info.get("source", "second_pass_validator"),
                label_consolidated_from=label_info.get("corrected_from"),
            )
            
            label_entities.append(label_entity.to_item())
            
        except Exception as e:
            logger.error(f"Failed to create ReceiptWordLabel for word {word_id}: {e}")
            continue
    
    return label_entities


@tool
async def batch_write_labels_to_dynamodb(
    label_entities: List[Dict],
    table_name: str
) -> Dict[str, Any]:
    """Batch write label entities to DynamoDB.
    
    Args:
        label_entities: List of ReceiptWordLabel DynamoDB items
        table_name: DynamoDB table name
        
    Returns:
        Dict with write results and statistics
    """
    if not label_entities:
        return {"labels_written": 0, "batch_count": 0, "errors": []}
    
    client_manager = get_client_manager()
    
    # DynamoDB batch write can handle max 25 items per batch
    batch_size = 25
    batches = [
        label_entities[i:i + batch_size]
        for i in range(0, len(label_entities), batch_size)
    ]
    
    total_written = 0
    errors = []
    
    for batch_num, batch in enumerate(batches):
        try:
            # Prepare batch write request
            request_items = {
                table_name: [
                    {"PutRequest": {"Item": item}}
                    for item in batch
                ]
            }
            
            # Execute batch write
            response = client_manager.dynamo.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items (rare but possible)
            unprocessed = response.get("UnprocessedItems", {})
            if unprocessed:
                logger.warning(f"Batch {batch_num}: {len(unprocessed)} unprocessed items")
                # Could implement retry logic here
            
            batch_written = len(batch) - len(unprocessed.get(table_name, []))
            total_written += batch_written
            
            logger.info(f"Batch {batch_num + 1}/{len(batches)}: wrote {batch_written}/{len(batch)} labels")
            
        except Exception as e:
            logger.error(f"Failed to write batch {batch_num}: {e}")
            errors.append({
                "batch_num": batch_num,
                "error": str(e),
                "items_count": len(batch)
            })
    
    return {
        "labels_written": total_written,
        "batch_count": len(batches),
        "total_requested": len(label_entities),
        "errors": errors,
        "success_rate": total_written / len(label_entities) if label_entities else 0,
    }


@tool
async def update_receipt_validation_summary(
    receipt_id: int,
    image_id: str,
    validation_metrics: Dict[str, Any],
    table_name: str
) -> Dict[str, Any]:
    """Update receipt-level validation summary in DynamoDB.
    
    Args:
        receipt_id: Receipt identifier
        image_id: Image UUID
        validation_metrics: Validation metrics from second pass
        table_name: DynamoDB table name
        
    Returns:
        Dict with update results
    """
    client_manager = get_client_manager()
    
    try:
        # Create receipt validation summary item
        timestamp = datetime.now().isoformat()
        
        summary_item = {
            "PK": {"S": f"IMAGE#{image_id}"},
            "SK": {"S": f"RECEIPT#{receipt_id:05d}#VALIDATION_SUMMARY"},
            "TYPE": {"S": "RECEIPT_VALIDATION_SUMMARY"},
            "validation_timestamp": {"S": timestamp},
            "total_labels": {"N": str(validation_metrics.get("validated_labels_count", 0))},
            "essential_coverage": {"N": str(validation_metrics.get("essential_coverage", 0))},
            "validation_rate": {"N": str(validation_metrics.get("validation_rate", 0))},
            "quality_score": {"N": str(validation_metrics.get("quality_score", 0))},
            "essential_fields_found": {
                "SS": validation_metrics.get("essential_fields_found", [])
            } if validation_metrics.get("essential_fields_found") else {"NULL": True},
            "essential_fields_missing": {
                "SS": validation_metrics.get("essential_fields_missing", [])
            } if validation_metrics.get("essential_fields_missing") else {"NULL": True},
            "labels_needing_review": {"N": str(validation_metrics.get("labels_needing_review", 0))},
        }
        
        # Write summary item
        client_manager.dynamo.put_item(
            TableName=table_name,
            Item=summary_item
        )
        
        return {
            "summary_written": True,
            "receipt_id": receipt_id,
            "validation_timestamp": timestamp
        }
        
    except Exception as e:
        logger.error(f"Failed to write validation summary for receipt {receipt_id}: {e}")
        return {
            "summary_written": False,
            "error": str(e)
        }


@tool
async def verify_persistence_success(
    validated_labels: Dict[int, Dict],
    receipt_id: int,
    image_id: str,
    table_name: str
) -> Dict[str, Any]:
    """Verify that labels were successfully written to DynamoDB.
    
    Args:
        validated_labels: Original validated labels
        receipt_id: Receipt identifier
        image_id: Image UUID
        table_name: DynamoDB table name
        
    Returns:
        Dict with verification results
    """
    client_manager = get_client_manager()
    
    verified_count = 0
    missing_labels = []
    
    # Sample a few labels to verify (not all, for performance)
    sample_word_ids = list(validated_labels.keys())[:5]  # Check first 5
    
    for word_id in sample_word_ids:
        try:
            # Query for the specific label
            response = client_manager.dynamo.get_item(
                TableName=table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#LINE#00001#WORD#{word_id:05d}#LABEL#{validated_labels[word_id]['label']}"}
                }
            )
            
            if "Item" in response:
                verified_count += 1
            else:
                missing_labels.append(word_id)
                
        except Exception as e:
            logger.warning(f"Failed to verify word_id {word_id}: {e}")
            missing_labels.append(word_id)
    
    verification_rate = verified_count / len(sample_word_ids) if sample_word_ids else 0
    
    return {
        "verified_count": verified_count,
        "sample_size": len(sample_word_ids),
        "missing_labels": missing_labels,
        "verification_rate": verification_rate,
        "verification_passed": verification_rate >= 0.8  # 80% threshold
    }


async def persist_to_dynamo_node(state: ReceiptProcessingState) -> ReceiptProcessingState:
    """Persist validated labels to DynamoDB with comprehensive error handling.
    
    This node:
    1. Creates ReceiptWordLabel entities from validated labels
    2. Batch writes labels to DynamoDB
    3. Creates receipt-level validation summary
    4. Verifies persistence success
    
    Based on the validation pipeline pattern from infra/validation_pipeline/lambda.py
    
    Context Engineering:
    - WRITE: dynamo_status to state
    - ISOLATE: Persistence logic separate from validation
    """
    logger.info("Starting persistence of validated labels to DynamoDB")
    
    # Get inputs from state
    validated_labels = state.get("validated_labels", {})
    validation_metrics = state.get("validation_metrics", {})
    receipt_words = state.get("receipt_words", [])
    receipt_id = state.get("receipt_id")
    image_id = state.get("image_id")
    
    if not validated_labels:
        logger.warning("No validated labels to persist")
        state["dynamo_status"] = "no_labels"
        return state
    
    if not receipt_id or not image_id:
        logger.error("Missing receipt_id or image_id for persistence")
        state["dynamo_status"] = "missing_identifiers"
        return state
    
    # Get table name from environment or client manager
    table_name = "ReceiptsTable"  # This should come from environment config
    
    try:
        # 1. Create ReceiptWordLabel entities
        logger.info(f"Creating {len(validated_labels)} ReceiptWordLabel entities")
        label_entities = await create_receipt_word_label_entities.ainvoke({
            "validated_labels": validated_labels,
            "receipt_id": receipt_id,
            "image_id": image_id,
            "receipt_words": receipt_words
        })
        
        if not label_entities:
            logger.error("Failed to create any label entities")
            state["dynamo_status"] = "entity_creation_failed"
            return state
        
        # 2. Batch write to DynamoDB
        logger.info(f"Writing {len(label_entities)} labels to DynamoDB")
        write_results = await batch_write_labels_to_dynamodb.ainvoke({
            "label_entities": label_entities,
            "table_name": table_name
        })
        
        # 3. Write validation summary
        logger.info("Writing validation summary to DynamoDB")
        summary_results = await update_receipt_validation_summary.ainvoke({
            "receipt_id": receipt_id,
            "image_id": image_id,
            "validation_metrics": validation_metrics,
            "table_name": table_name
        })
        
        # 4. Verify persistence success
        logger.info("Verifying persistence success")
        verification_results = await verify_persistence_success.ainvoke({
            "validated_labels": validated_labels,
            "receipt_id": receipt_id,
            "image_id": image_id,
            "table_name": table_name
        })
        
        # Determine overall status
        persistence_success = (
            write_results["success_rate"] >= 0.9 and
            summary_results["summary_written"] and
            verification_results["verification_passed"]
        )
        
        # Update state with comprehensive results
        state["dynamo_status"] = "persisted" if persistence_success else "partial_failure"
        state["persistence_results"] = {
            "labels_written": write_results["labels_written"],
            "total_requested": write_results["total_requested"],
            "write_success_rate": write_results["success_rate"],
            "summary_written": summary_results["summary_written"],
            "verification_passed": verification_results["verification_passed"],
            "verification_rate": verification_results["verification_rate"],
            "errors": write_results["errors"],
        }
        
        # Add decision tracking
        state["decisions"].append({
            "node": "persist_to_dynamo",
            "action": "persisted_labels",
            "labels_written": write_results["labels_written"],
            "total_requested": write_results["total_requested"],
            "success_rate": f"{write_results['success_rate']:.1%}",
            "summary_written": summary_results["summary_written"],
            "verification_passed": verification_results["verification_passed"],
            "status": state["dynamo_status"],
            "timestamp": datetime.now().isoformat(),
        })
        
        if persistence_success:
            logger.info(
                f"Successfully persisted {write_results['labels_written']} labels "
                f"({write_results['success_rate']:.1%} success rate)"
            )
        else:
            logger.warning(
                f"Partial persistence failure: {write_results['labels_written']}/{write_results['total_requested']} "
                f"labels written, verification: {verification_results['verification_passed']}"
            )
    
    except Exception as e:
        logger.error(f"Failed to persist labels to DynamoDB: {e}")
        state["dynamo_status"] = "error"
        state["persistence_error"] = str(e)
        
        # Add error tracking
        state["decisions"].append({
            "node": "persist_to_dynamo",
            "action": "persistence_error",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        })
    
    return state