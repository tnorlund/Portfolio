import json
import logging
import os
from typing import Any, Dict

from receipt_label import ReceiptLabeler
from receipt_label.models.validation import ValidationStatus

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]


def extract_validation_details(validation_analysis):
    """Extract detailed validation information from a ValidationAnalysis object."""
    if not validation_analysis:
        return None

    validation_status = validation_analysis.overall_status
    logger.info(f"Validation status: {validation_status}")

    if validation_status == ValidationStatus.VALID.value:
        logger.info("Receipt validation passed all checks")
    elif validation_status == ValidationStatus.INVALID.value:
        logger.error("Receipt validation failed critical checks")
    elif validation_status == ValidationStatus.NEEDS_REVIEW.value:
        logger.warning("Receipt requires human review")

    if (
        hasattr(validation_analysis, "overall_reasoning")
        and validation_analysis.overall_reasoning
    ):
        logger.info(
            f"Validation reasoning: {validation_analysis.overall_reasoning}"
        )

    return {
        "status": validation_status,
        "reasoning": getattr(validation_analysis, "overall_reasoning", None),
    }


def handler(event: Dict[str, Any], _) -> Dict[str, Any]:
    """Lambda handler to process a single receipt.

    Args:
        event: Lambda event data containing receipt_id and image_id
        _: Lambda context object (unused)

    Returns:
        Dict containing the processing result
    """
    logger.info(f"Starting receipt processing for event: {json.dumps(event)}")

    # Extract receipt information from the event
    try:
        receipt_id = event["receipt_id"]
        image_id = event["image_id"]

        # Initialize client
        client = DynamoClient(DYNAMODB_TABLE_NAME)

        logger.info(f"Processing receipt {receipt_id} from image {image_id}")

        # Check if analysis already exists
        existing_analysis = client.getReceiptAnalysis(
            image_id, int(receipt_id)
        )

        # Only process if there's no complete analysis already
        if (
            existing_analysis
            and existing_analysis.label_analysis
            and existing_analysis.structure_analysis
            and existing_analysis.line_item_analysis
            and existing_analysis.validation_summary
        ):

            logger.info(
                f"Analysis already exists for receipt {receipt_id}, returning existing analysis"
            )

            # Return summary of existing analysis
            return {
                "statusCode": 200,
                "receipt_id": receipt_id,
                "image_id": image_id,
                "success": True,
                "message": f"Receipt {receipt_id} already analyzed",
                "analysis_summary": {
                    "has_label_analysis": bool(
                        existing_analysis.label_analysis
                    ),
                    "has_structure_analysis": bool(
                        existing_analysis.structure_analysis
                    ),
                    "has_line_item_analysis": bool(
                        existing_analysis.line_item_analysis
                    ),
                    "has_validation_summary": bool(
                        existing_analysis.validation_summary
                    ),
                    "validation_categories_count": len(
                        existing_analysis.validation_categories
                    ),
                    "validation_results_count": len(
                        existing_analysis.validation_results
                    ),
                    "chatgpt_validations_count": len(
                        existing_analysis.chatgpt_validations
                    ),
                },
            }

        # Initialize labeler
        labeler = ReceiptLabeler(
            places_api_key=GOOGLE_PLACES_API_KEY,
            dynamodb_table_name=DYNAMODB_TABLE_NAME,
            gpt_api_key=OPENAI_API_KEY,
            validation_level="basic",
        )

        # Process the receipt using the package's built-in method
        logger.info(f"Calling process_receipt_by_id for receipt {receipt_id}")
        analysis_result = labeler.process_receipt_by_id(
            receipt_id=int(receipt_id),
            image_id=image_id,
            save_results=True,  # The package will handle saving all results
        )

        # Log analysis success and details
        if analysis_result:
            logger.info(f"Successfully processed receipt {receipt_id}")
            # Build response with detailed analysis information
            response = {
                "statusCode": 200,
                "receipt_id": receipt_id,
                "image_id": image_id,
                "success": True,
                "message": f"Successfully processed receipt {receipt_id}",
                "analysis_summary": {},
            }

            # Add field analysis details if available
            if (
                hasattr(analysis_result, "field_analysis")
                and analysis_result.field_analysis
            ):
                field_count = len(
                    getattr(analysis_result.field_analysis, "labels", [])
                )
                logger.info(
                    f"Field analysis found {field_count} labeled fields"
                )
                response["analysis_summary"]["field_count"] = field_count

            # Add structure analysis details if available
            if (
                hasattr(analysis_result, "structure_analysis")
                and analysis_result.structure_analysis
            ):
                section_count = len(
                    getattr(analysis_result.structure_analysis, "sections", [])
                )
                logger.info(
                    f"Structure analysis found {section_count} sections"
                )
                response["analysis_summary"]["section_count"] = section_count

            # Add line item analysis details if available
            if (
                hasattr(analysis_result, "line_item_analysis")
                and analysis_result.line_item_analysis
            ):
                item_count = len(
                    getattr(analysis_result.line_item_analysis, "items", [])
                )
                logger.info(
                    f"Line item analysis found {item_count} line items"
                )
                response["analysis_summary"]["line_item_count"] = item_count

                # Add financial details if available
                if hasattr(analysis_result.line_item_analysis, "subtotal"):
                    response["analysis_summary"][
                        "subtotal"
                    ] = analysis_result.line_item_analysis.subtotal
                if hasattr(analysis_result.line_item_analysis, "total"):
                    response["analysis_summary"][
                        "total"
                    ] = analysis_result.line_item_analysis.total
                if hasattr(analysis_result.line_item_analysis, "tax"):
                    response["analysis_summary"][
                        "tax"
                    ] = analysis_result.line_item_analysis.tax

            # Add validation details if available
            if (
                hasattr(analysis_result, "validation_analysis")
                and analysis_result.validation_analysis
            ):
                validation_details = extract_validation_details(
                    analysis_result.validation_analysis
                )
                if validation_details:
                    response["analysis_summary"][
                        "validation"
                    ] = validation_details

            return response
        else:
            return {
                "statusCode": 500,
                "receipt_id": receipt_id,
                "image_id": image_id,
                "success": False,
                "message": f"Failed to process receipt {receipt_id}: Analysis result is empty",
            }

    except Exception as e:
        logger.error(f"Error processing receipt: {str(e)}")
        return {
            "statusCode": 500,
            "receipt_id": event.get("receipt_id", "unknown"),
            "image_id": event.get("image_id", "unknown"),
            "error": str(e),
            "success": False,
            "message": f"Failed to process receipt: {str(e)}",
        }
