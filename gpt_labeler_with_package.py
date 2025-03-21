from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.receipt_label import ReceiptLabeler
from receipt_label.receipt_label.models.receipt import Receipt, ReceiptWord, ReceiptLine
from receipt_dynamo.data._gpt import (
    gpt_request_structure_analysis,
    gpt_request_field_labeling,
)
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.entities.util import normalize_address
import os
import json
from pathlib import Path
from time import sleep
import re
from datetime import datetime
import traceback
from dotenv import load_dotenv
import logging
import asyncio
import sys
from receipt_label.receipt_label.models.validation import (
    ValidationStatus,
    ValidationResultType,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file from root directory
load_dotenv()


def extract_validation_details(validation_analysis):
    """
    Extract detailed validation information from a ValidationAnalysis object.

    Args:
        validation_analysis: The ValidationAnalysis object from the receipt analysis

    Returns:
        Dict with validation details categorized by field and type
    """
    if not validation_analysis:
        return None

    details = {
        "overall_status": validation_analysis.overall_status,
        "overall_reasoning": validation_analysis.overall_reasoning,
        "field_validations": {},
        "error_count": 0,
        "warning_count": 0,
        "info_count": 0,
        "success_count": 0,
    }

    # Process individual field validations
    for field_name in [
        "business_identity",
        "address_verification",
        "phone_validation",
        "hours_verification",
        "cross_field_consistency",
        "line_item_validation",
    ]:
        if hasattr(validation_analysis, field_name):
            field_validation = getattr(validation_analysis, field_name)

            field_details = {
                "status": field_validation.status,
                "reasoning": field_validation.reasoning,
                "errors": [],
                "warnings": [],
                "info": [],
                "success": [],
            }

            # Extract individual results
            for result in field_validation.results:
                item = {
                    "message": result.message,
                    "reasoning": result.reasoning,
                    "field": result.field,
                    "expected_value": result.expected_value,
                    "actual_value": result.actual_value,
                }

                if result.type == "error":
                    field_details["errors"].append(item)
                    details["error_count"] += 1
                elif result.type == "warning":
                    field_details["warnings"].append(item)
                    details["warning_count"] += 1
                elif result.type == "info":
                    field_details["info"].append(item)
                    details["info_count"] += 1
                elif result.type == "success":
                    field_details["success"].append(item)
                    details["success_count"] += 1

            details["field_validations"][field_name] = field_details

    return details


def validate_environment():
    """Validate required environment variables are set and valid."""
    required_vars = {
        "GOOGLE_PLACES_API_KEY": "Google Places API key for business validation",
        "OPENAI_API_KEY": "OpenAI API key for GPT-based processing",
        "AWS_ACCESS_KEY_ID": "AWS access key for DynamoDB access",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key for DynamoDB access",
        "AWS_DEFAULT_REGION": "AWS region for DynamoDB access",
    }

    missing_vars = []
    invalid_vars = []

    for var_name, description in required_vars.items():
        value = os.getenv(var_name)
        if not value:
            missing_vars.append(f"{var_name} ({description})")
        elif (
            var_name.endswith("_KEY") and len(value) < 20
        ):  # Basic validation for API keys
            invalid_vars.append(f"{var_name} (appears to be invalid)")

    if missing_vars or invalid_vars:
        error_msg = []
        if missing_vars:
            error_msg.append("Missing required environment variables:")
            for var in missing_vars:
                error_msg.append(f"  - {var}")

        if invalid_vars:
            error_msg.append("\nInvalid environment variables:")
            for var in invalid_vars:
                error_msg.append(f"  - {var}")

        error_msg.append(
            "\nPlease set these variables in your .env file or environment."
        )
        error_msg.append("Example .env file:")
        error_msg.append(
            """
GOOGLE_PLACES_API_KEY=your_google_places_api_key
OPENAI_API_KEY=your_openai_api_key
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_DEFAULT_REGION=your_aws_region
"""
        )

        logger.error("\n".join(error_msg))
        sys.exit(1)


def process_receipt(labeler, receipt_obj, receipt_words, receipt_lines):
    """Process a single receipt asynchronously."""
    # Perform standard labeling with validation included
    analysis_result = labeler.label_receipt(
        receipt_obj, receipt_words, receipt_lines
    )

    # Log the available attributes of the analysis_result for debugging
    logger.info(f"Analysis result has the following attributes: {dir(analysis_result)}")

    # Check if field_analysis is available
    if hasattr(analysis_result, "field_analysis"):
        logger.info("Field analysis is available")

    # Check if line_item_analysis is available
    if hasattr(analysis_result, "line_item_analysis"):
        logger.info("Line item analysis is available")

        # Only log values that were explicitly labeled, not inferred ones
        found_values = {"subtotal": None, "tax": None, "total": None}

        # Look for explicitly labeled financial values in the field analysis
        if hasattr(analysis_result, "field_analysis") and hasattr(
            analysis_result.field_analysis, "labels"
        ):
            for label in analysis_result.field_analysis.labels:
                if label.label in ["SUBTOTAL", "Subtotal", "subtotal"]:
                    try:
                        text_value = label.text.replace("$", "").replace(",", "")
                        found_values["subtotal"] = float(text_value)
                        logger.info(
                            f"Subtotal (from labels): {found_values['subtotal']}"
                        )
                    except (ValueError, TypeError):
                        pass

                elif label.label in ["TAX", "Tax", "tax"]:
                    try:
                        text_value = label.text.replace("$", "").replace(",", "")
                        found_values["tax"] = float(text_value)
                        logger.info(f"Tax (from labels): {found_values['tax']}")
                    except (ValueError, TypeError):
                        pass

                elif label.label in ["TOTAL", "Total", "total"]:
                    try:
                        text_value = label.text.replace("$", "").replace(",", "")
                        found_values["total"] = float(text_value)
                        logger.info(f"Total (from labels): {found_values['total']}")
                    except (ValueError, TypeError):
                        pass

        # Only log labeled values, not inferred ones
        if hasattr(analysis_result.line_item_analysis, "subtotal"):
            logger.info(
                f"Subtotal (possibly inferred): {analysis_result.line_item_analysis.subtotal}"
            )
        if hasattr(analysis_result.line_item_analysis, "tax"):
            logger.info(
                f"Tax (possibly inferred): {analysis_result.line_item_analysis.tax}"
            )
        if hasattr(analysis_result.line_item_analysis, "total"):
            logger.info(
                f"Total (possibly inferred): {analysis_result.line_item_analysis.total}"
            )

    # Check if validation_analysis is available
    if (
        hasattr(analysis_result, "validation_analysis")
        and analysis_result.validation_analysis
    ):
        logger.info("Validation analysis is available")
        logger.info(
            f"Validation status: {analysis_result.validation_analysis.overall_status}"
        )
    else:
        logger.warning("No validation analysis available in the result")

    return analysis_result


def main():
    logger.info("Starting receipt analysis...")

    # Process receipts
    total_receipts = 0
    successful_analyses = 0
    total_structure_confidence = 0
    total_word_confidence = 0

    try:
        # Validate environment variables first
        validate_environment()

        # Load environment and create clients
        env = load_env("dev")
        client = DynamoClient(env["dynamodb_table_name"])

        # Initialize the ReceiptLabeler with proper validation configuration
        labeler = ReceiptLabeler(
            places_api_key=os.getenv("GOOGLE_PLACES_API_KEY"),
            dynamodb_table_name=env["dynamodb_table_name"],
            gpt_api_key=os.getenv("OPENAI_API_KEY"),
            validation_level="basic",
        )

        # Create output directory
        output_dir = Path("analysis_results")
        output_dir.mkdir(exist_ok=True)

        # Track statistics
        stats = {
            "total_receipts": 0,
            "successful_analysis": 0,
            "section_types": {},
            "reasoning_provided": 0,
            "word_label_stats": {
                "total_words": 0,
                "labeled_words": 0,
                "label_distribution": {},
            },
            "errors": [],
            "validation_stats": {
                "valid_count": 0,
                "invalid_count": 0,
                "needs_review_count": 0,
                "incomplete_count": 0,
                "field_status_counts": {
                    "business_identity": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                    "address_verification": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                    "phone_validation": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                    "hours_verification": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                    "cross_field_consistency": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                    "line_item_validation": {
                        "valid": 0,
                        "invalid": 0,
                        "needs_review": 0,
                        "incomplete": 0,
                    },
                },
                "common_errors": {},
                "common_warnings": {},
            },
        }

        # Get sample receipts
        logger.info("Getting receipts...")
        last_evaluated_key = {
            "PK": {"S": "IMAGE#5c13b5c3-2244-4ea6-8872-925839ab22f7"},
            "SK": {"S": "RECEIPT#00001"},
            "TYPE": {"S": "RECEIPT"},
        }
        receipts, last_evaluated_key = client.listReceipts(
            limit=3, lastEvaluatedKey=last_evaluated_key
        )  # Analyze 3 receipts
        logger.info(f"Last evaluated key: {last_evaluated_key}")
        stats["total_receipts"] = len(receipts)

        # Process each receipt
        for receipt_num, receipt in enumerate(receipts, 1):
            logger.info(
                f"\nProcessing receipt {receipt_num}/{len(receipts)} [image_id: {receipt.image_id}, receipt_id: {receipt.receipt_id}]..."
            )

            try:
                # Get receipt details including lines
                (
                    receipt_data,
                    receipt_lines_data,
                    receipt_words_data,
                    receipt_letters,
                    validations,
                ) = client.getReceiptDetails(receipt.image_id, receipt.receipt_id)

                # Convert DynamoDB objects to receipt_label objects
                try:
                    # Convert DynamoReceiptWord and DynamoReceiptLine objects using from_dynamo
                    receipt_words = [
                        ReceiptWord.from_dynamo(word) for word in receipt_words_data
                    ]
                    receipt_lines = [
                        ReceiptLine.from_dynamo(line) for line in receipt_lines_data
                    ]

                    # Create Receipt object from DynamoDB data
                    receipt_obj = Receipt.from_dynamo(
                        receipt.receipt_id,
                        receipt.image_id,
                        receipt_words,
                        receipt_lines,
                    )
                except Exception as e:
                    logger.error(f"Error converting data to objects: {str(e)}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    raise

                # Analyze receipt using ReceiptLabeler
                analysis_result = process_receipt(
                    labeler, receipt_obj, receipt_words, receipt_lines
                )

                # Update statistics
                stats["successful_analysis"] += 1

                # Track section types
                try:
                    for section in analysis_result.structure_analysis.sections:
                        section_name = section.name
                        if section_name not in stats["section_types"]:
                            stats["section_types"][section_name] = {
                                "count": 0,
                                "spatial_patterns": {},
                                "content_patterns": {},
                            }

                        section_stats = stats["section_types"][section_name]
                        section_stats["count"] += 1

                        # Track patterns
                        for pattern in section.spatial_patterns:
                            pattern_desc = (
                                pattern.description
                                if hasattr(pattern, "description")
                                else str(pattern)
                            )
                            section_stats["spatial_patterns"][pattern_desc] = (
                                section_stats["spatial_patterns"].get(pattern_desc, 0)
                                + 1
                            )
                        for pattern in section.content_patterns:
                            pattern_desc = (
                                pattern.description
                                if hasattr(pattern, "description")
                                else str(pattern)
                            )
                            section_stats["content_patterns"][pattern_desc] = (
                                section_stats["content_patterns"].get(pattern_desc, 0)
                                + 1
                            )
                except (KeyError, TypeError) as e:
                    logger.warning(f"Could not process section statistics: {str(e)}")

                # Update word label statistics
                try:
                    stats["word_label_stats"]["total_words"] += len(receipt_words)
                    stats["word_label_stats"]["labeled_words"] += len(
                        analysis_result.field_analysis.labels
                    )

                    # Track label distribution
                    for label_info in analysis_result.field_analysis.labels:
                        label_type = label_info.label
                        if (
                            label_type
                            not in stats["word_label_stats"]["label_distribution"]
                        ):
                            stats["word_label_stats"]["label_distribution"][
                                label_type
                            ] = {
                                "count": 0,
                            }
                        stats["word_label_stats"]["label_distribution"][label_type][
                            "count"
                        ] += 1
                except (KeyError, TypeError) as e:
                    logger.warning(f"Could not process word label statistics: {str(e)}")

                # Update reasoning tracking
                # We're not tracking confidence scores anymore, so we'll count successful analyses with reasoning
                if analysis_result.field_analysis and hasattr(
                    analysis_result.field_analysis, "labels"
                ):
                    has_reasoning = any(
                        hasattr(label, "reasoning") and label.reasoning
                        for label in analysis_result.field_analysis.labels
                    )
                    if has_reasoning:
                        stats["reasoning_provided"] += 1

                # Update validation statistics if validation analysis is available
                if (
                    hasattr(analysis_result, "validation_analysis")
                    and analysis_result.validation_analysis
                ):
                    validation_status = (
                        analysis_result.validation_analysis.overall_status
                    )

                    # Map validation status to stats counter
                    if validation_status == "valid":
                        stats["validation_stats"]["valid_count"] += 1
                    elif validation_status == "invalid":
                        stats["validation_stats"]["invalid_count"] += 1
                    elif validation_status == "needs_review":
                        stats["validation_stats"]["needs_review_count"] += 1
                    elif validation_status == "incomplete":
                        stats["validation_stats"]["incomplete_count"] += 1

                    # Log validation status and reasoning
                    logger.info(
                        f"Validation status for receipt {receipt.receipt_id}: {validation_status}"
                    )
                    if (
                        hasattr(
                            analysis_result.validation_analysis, "overall_reasoning"
                        )
                        and analysis_result.validation_analysis.overall_reasoning
                    ):
                        logger.info(
                            f"Validation reasoning: {analysis_result.validation_analysis.overall_reasoning}"
                        )

                    # Extract and log detailed validation information
                    validation_details = extract_validation_details(
                        analysis_result.validation_analysis
                    )
                    if validation_details and validation_status != "valid":
                        logger.info(
                            f"Detailed validation issues for receipt {receipt.receipt_id}:"
                        )

                        # Log validation issues by field
                        for field_name, field_details in validation_details[
                            "field_validations"
                        ].items():
                            # Update field-level status statistics
                            field_status = field_details["status"]
                            if field_status in [
                                "valid",
                                "invalid",
                                "needs_review",
                                "incomplete",
                            ]:
                                stats["validation_stats"]["field_status_counts"][
                                    field_name
                                ][field_status] += 1

                            if field_details["status"] != "valid":
                                logger.info(
                                    f"  - {field_name.replace('_', ' ').title()} ({field_details['status']}): {field_details['reasoning']}"
                                )

                                # Log errors
                                for error in field_details["errors"]:
                                    # Track common errors
                                    error_key = f"{field_name}:{error['message']}"
                                    if (
                                        error_key
                                        not in stats["validation_stats"][
                                            "common_errors"
                                        ]
                                    ):
                                        stats["validation_stats"]["common_errors"][
                                            error_key
                                        ] = {
                                            "count": 0,
                                            "field_name": field_name,
                                            "message": error["message"],
                                            "examples": [],
                                        }

                                    stats["validation_stats"]["common_errors"][
                                        error_key
                                    ]["count"] += 1
                                    if (
                                        len(
                                            stats["validation_stats"]["common_errors"][
                                                error_key
                                            ]["examples"]
                                        )
                                        < 3
                                    ):
                                        example = {
                                            "receipt_id": receipt.receipt_id,
                                            "reasoning": error["reasoning"],
                                        }
                                        stats["validation_stats"]["common_errors"][
                                            error_key
                                        ]["examples"].append(example)

                                    logger.error(
                                        f"      ERROR: {error['message']} - {error['reasoning']}"
                                    )
                                    if error["field"]:
                                        logger.error(
                                            f"             Field: {error['field']}"
                                        )
                                    if (
                                        error["expected_value"] is not None
                                        and error["actual_value"] is not None
                                    ):
                                        logger.error(
                                            f"             Expected: {error['expected_value']}, Actual: {error['actual_value']}"
                                        )

                                # Log warnings
                                for warning in field_details["warnings"]:
                                    # Track common warnings
                                    warning_key = f"{field_name}:{warning['message']}"
                                    if (
                                        warning_key
                                        not in stats["validation_stats"][
                                            "common_warnings"
                                        ]
                                    ):
                                        stats["validation_stats"]["common_warnings"][
                                            warning_key
                                        ] = {
                                            "count": 0,
                                            "field_name": field_name,
                                            "message": warning["message"],
                                            "examples": [],
                                        }

                                    stats["validation_stats"]["common_warnings"][
                                        warning_key
                                    ]["count"] += 1
                                    if (
                                        len(
                                            stats["validation_stats"][
                                                "common_warnings"
                                            ][warning_key]["examples"]
                                        )
                                        < 3
                                    ):
                                        example = {
                                            "receipt_id": receipt.receipt_id,
                                            "reasoning": warning["reasoning"],
                                        }
                                        stats["validation_stats"]["common_warnings"][
                                            warning_key
                                        ]["examples"].append(example)

                                    logger.warning(
                                        f"      WARNING: {warning['message']} - {warning['reasoning']}"
                                    )

                        # Save validation details to a file for this receipt
                        validation_file = (
                            output_dir
                            / f"validation_details_{receipt.image_id}_{receipt.receipt_id}.json"
                        )
                        with open(validation_file, "w") as f:
                            json.dump(validation_details, f, indent=2)
                else:
                    # If no validation analysis is available, simply log it
                    logger.info(
                        f"No validation analysis available for receipt {receipt.receipt_id}"
                    )
                    # Mark receipt as incomplete for validation tracking
                    stats["validation_stats"]["incomplete_count"] += 1
                    # Update field-level status statistics for all fields to incomplete
                    for field_name in stats["validation_stats"]["field_status_counts"]:
                        stats["validation_stats"]["field_status_counts"][field_name][
                            "incomplete"
                        ] += 1

            except Exception as e:
                error_context = {
                    "receipt_id": receipt.receipt_id,
                    "image_id": receipt.image_id,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc(),
                }
                logger.error(f"\nError processing receipt:")
                logger.error(f"Receipt ID: {receipt.receipt_id}")
                logger.error(f"Image ID: {receipt.image_id}")
                logger.error(f"Error Message: {str(e)}")
                stats["errors"].append(error_context)
                # Mark this receipt as incomplete for validation tracking
                stats["validation_stats"]["incomplete_count"] += 1
                # Update field-level status statistics for all fields to incomplete
                for field_name in stats["validation_stats"]["field_status_counts"]:
                    stats["validation_stats"]["field_status_counts"][field_name][
                        "incomplete"
                    ] += 1
                continue

        # Calculate final statistics
        if stats["successful_analysis"] > 0:
            # We don't calculate avg_confidence anymore
            pass  # Remove all the confidence calculations

            # Instead, calculate percentage of analyses with reasoning
            reasoning_percentage = (
                stats["reasoning_provided"] / stats["successful_analysis"]
            ) * 100

        # Save statistics
        stats_file = output_dir / "analysis_statistics.json"
        with open(stats_file, "w") as f:
            json.dump(stats, f, indent=2)

        # Print summary
        logger.info("\n%s", "=" * 50)
        logger.info("ANALYSIS SUMMARY")
        logger.info("%s\n", "=" * 50)
        logger.info("Total receipts processed: %d", stats["total_receipts"])
        logger.info("Successful analyses: %d", stats["successful_analysis"])
        logger.info("Analyses with reasoning: %.1f%%", reasoning_percentage)
        logger.info("\nWord-Level Statistics:")
        logger.info(
            "Total words processed: %d", stats["word_label_stats"]["total_words"]
        )
        logger.info("Words labeled: %d", stats["word_label_stats"]["labeled_words"])
        if stats["word_label_stats"]["labeled_words"] > 0:
            labeling_percentage = (
                stats["word_label_stats"]["labeled_words"]
                / stats["word_label_stats"]["total_words"]
            ) * 100
            logger.info("Word labeling coverage: %.1f%%", labeling_percentage)

        # Print validation summary
        logger.info("\nValidation Summary:")
        logger.info(
            "Total valid receipts: %d/%d (%.1f%%)",
            stats["validation_stats"]["valid_count"],
            stats["total_receipts"],
            (
                (
                    stats["validation_stats"]["valid_count"]
                    / stats["total_receipts"]
                    * 100
                )
                if stats["total_receipts"] > 0
                else 0
            ),
        )

        if stats["validation_stats"]["invalid_count"] > 0:
            logger.error(
                "Invalid receipts: %d (%.1f%%)",
                stats["validation_stats"]["invalid_count"],
                (
                    (
                        stats["validation_stats"]["invalid_count"]
                        / stats["total_receipts"]
                        * 100
                    )
                    if stats["total_receipts"] > 0
                    else 0
                ),
            )
            logger.error("  Most common reason: Missing total amount")

        if stats["validation_stats"]["needs_review_count"] > 0:
            logger.warning(
                "Receipts needing review: %d (%.1f%%)",
                stats["validation_stats"]["needs_review_count"],
                (
                    (
                        stats["validation_stats"]["needs_review_count"]
                        / stats["total_receipts"]
                        * 100
                    )
                    if stats["total_receipts"] > 0
                    else 0
                ),
            )
            logger.warning(
                "  These receipts have non-critical issues like financial discrepancies"
            )

        if stats["validation_stats"]["incomplete_count"] > 0:
            logger.warning(
                "Incomplete receipts: %d (%.1f%%)",
                stats["validation_stats"]["incomplete_count"],
                (
                    (
                        stats["validation_stats"]["incomplete_count"]
                        / stats["total_receipts"]
                        * 100
                    )
                    if stats["total_receipts"] > 0
                    else 0
                ),
            )
            logger.warning(
                "  These receipts couldn't be validated due to missing data or processing errors"
            )

        # Add detailed field-level validation statistics
        logger.info("\nField-Level Validation Results:")
        for field_name, status_counts in stats["validation_stats"][
            "field_status_counts"
        ].items():
            valid_count = status_counts["valid"]
            total_with_field = sum(status_counts.values())

            if total_with_field > 0:
                valid_percent = (valid_count / total_with_field) * 100
                logger.info(
                    f"  {field_name.replace('_', ' ').title()}: {valid_count}/{total_with_field} valid ({valid_percent:.1f}%)"
                )

                if status_counts["invalid"] > 0:
                    logger.error(f"    - Invalid: {status_counts['invalid']}")
                if status_counts["needs_review"] > 0:
                    logger.warning(
                        f"    - Needs review: {status_counts['needs_review']}"
                    )
                if status_counts["incomplete"] > 0:
                    logger.warning(f"    - Incomplete: {status_counts['incomplete']}")

        # List common validation errors
        if stats["validation_stats"]["common_errors"]:
            logger.info("\nMost Common Validation Errors:")

            # Sort errors by frequency
            sorted_errors = sorted(
                stats["validation_stats"]["common_errors"].values(),
                key=lambda x: x["count"],
                reverse=True,
            )

            # Display top 5 errors
            for i, error in enumerate(sorted_errors[:5], 1):
                logger.error(
                    f"{i}. {error['field_name'].replace('_', ' ').title()}: {error['message']} ({error['count']} occurrences)"
                )
                if error["examples"]:
                    example = error["examples"][0]
                    logger.error(
                        f"   Example (Receipt {example['receipt_id']}): {example['reasoning']}"
                    )

        # List common validation warnings
        if stats["validation_stats"]["common_warnings"]:
            logger.info("\nMost Common Validation Warnings:")

            # Sort warnings by frequency
            sorted_warnings = sorted(
                stats["validation_stats"]["common_warnings"].values(),
                key=lambda x: x["count"],
                reverse=True,
            )

            # Display top 5 warnings
            for i, warning in enumerate(sorted_warnings[:5], 1):
                logger.warning(
                    f"{i}. {warning['field_name'].replace('_', ' ').title()}: {warning['message']} ({warning['count']} occurrences)"
                )
                if warning["examples"]:
                    example = warning["examples"][0]
                    logger.warning(
                        f"   Example (Receipt {example['receipt_id']}): {example['reasoning']}"
                    )

        return 0

    except Exception as e:
        logger.error("Fatal error in receipt processing: %s", str(e))
        return 1


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nScript interrupted by user. Exiting...")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
