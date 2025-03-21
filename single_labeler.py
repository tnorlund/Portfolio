#!/usr/bin/env python3
"""
Single Receipt Labeler Script

This script processes a single receipt by ID, performs analysis using the ReceiptLabeler,
and saves all analysis results back to DynamoDB.

Usage:
    python single_labeler.py --receipt_id RECEIPT_ID --image_id IMAGE_ID

Example:
    python single_labeler.py --receipt_id "00001" --image_id "5c13b5c3-2244-4ea6-8872-925839ab22f7"
"""

import os
import json
import logging
import argparse
import time  # Replace asyncio with time
import traceback
import sys
from pathlib import Path
from dotenv import load_dotenv


from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_label.receipt_label import ReceiptLabeler
from receipt_label.receipt_label.models.receipt import Receipt, ReceiptWord, ReceiptLine
from receipt_label.receipt_label.models.validation import ValidationStatus

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Load .env file from root directory
load_dotenv()

# Placeholder functions have been removed as they're now properly implemented
# in receipt_label/receipt_label/data/analysis_operations.py


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
        logger.error("\n".join(error_msg))
        sys.exit(1)


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
        logger.info(f"Validation reasoning: {validation_analysis.overall_reasoning}")

    return {
        "status": validation_status,
        "reasoning": getattr(validation_analysis, "overall_reasoning", None),
    }


def analyze_single_receipt(receipt_id, image_id, output_dir=None):
    """Analyze a single receipt by ID and save results to DynamoDB."""
    try:
        # Validate environment variables
        validate_environment()

        # Load environment and create clients
        env = load_env("dev")
        table_name = env["dynamodb_table_name"]

        # Initialize ReceiptLabeler
        labeler = ReceiptLabeler(
            places_api_key=os.getenv("GOOGLE_PLACES_API_KEY"),
            dynamodb_table_name=table_name,
            gpt_api_key=os.getenv("OPENAI_API_KEY"),
            validation_level="basic",
        )

        # Create output directory if provided
        if output_dir:
            output_dir = Path(output_dir)
            output_dir.mkdir(exist_ok=True)

        logger.info(f"Processing receipt {receipt_id} from image {image_id}")
        # Get the analysis results from DynamoDB
        analysis_result = DynamoClient(table_name=table_name).getReceiptAnalysis(
            image_id, int(receipt_id)
        )
        if analysis_result.label_analysis:
            DynamoClient(table_name=table_name).deleteReceiptLabelAnalysis(
                analysis_result.label_analysis
            )
            logger.info("Deleted existing label analysis")
        if analysis_result.structure_analysis:
            DynamoClient(table_name=table_name).deleteReceiptStructureAnalysis(
                analysis_result.structure_analysis
            )
            logger.info("Deleted existing structure analysis")
        if analysis_result.line_item_analysis:
            DynamoClient(table_name=table_name).deleteReceiptLineItemAnalysis(
                analysis_result.line_item_analysis
            )
            logger.info("Deleted existing line item analysis")
        if analysis_result.validation_summary:
            DynamoClient(table_name=table_name).deleteReceiptValidationSummary(
                analysis_result.validation_summary
            )
            logger.info("Deleted existing validation summary")
        if len(analysis_result.validation_categories) > 0:
            for validation_category in analysis_result.validation_categories:
                print(f"validation_category: {validation_category}")
                DynamoClient(table_name=table_name).deleteReceiptValidationCategory(
                    validation_category
                )
            logger.info(
                f"Deleted {len(analysis_result.validation_categories)} existing validation categories"
            )
        if len(analysis_result.validation_results) > 0:
            for validation_result in analysis_result.validation_results:
                print(f"validation_result: {validation_result}")
                DynamoClient(table_name=table_name).deleteReceiptValidationResult(
                    validation_result
                )
            logger.info(
                f"Deleted {len(analysis_result.validation_results)} existing validation results"
            )
        if len(analysis_result.chatgpt_validations) > 0:
            for chatgpt_validation in analysis_result.chatgpt_validations:
                print(f"chatgpt_validation: {chatgpt_validation}")
                DynamoClient(table_name=table_name).deleteReceiptChatGPTValidation(
                    chatgpt_validation
                )
            logger.info(
                f"Deleted {len(analysis_result.chatgpt_validations)} existing ChatGPT validations"
            )

        # Add a small delay to ensure deletions are processed
        logger.info("Waiting for deletion operations to complete...")
        time.sleep(1)  # Use time.sleep instead of asyncio.sleep

        # Process receipt using the new method that handles data retrieval and saving
        try:
            # This single call now handles:
            # 1. Retrieving receipt data from DynamoDB
            # 2. Converting DynamoDB objects to model objects
            # 3. Processing the receipt
            # 4. Saving results back to DynamoDB
            analysis_result = labeler.process_receipt_by_id(  # Change to sync version
                receipt_id=int(receipt_id),
                image_id=image_id,
                save_results=True,  # Re-enable automatic saving
            )
        except Exception as e:
            logger.error(f"Error processing receipt: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

        # Log analysis results
        logger.info("Receipt analysis completed successfully")

        # Check field analysis
        if (
            hasattr(analysis_result, "field_analysis")
            and analysis_result.field_analysis
        ):
            field_count = len(getattr(analysis_result.field_analysis, "labels", []))
            logger.info(f"Field analysis found {field_count} labeled fields")
        else:
            logger.warning("No field analysis available")

        # Check structure analysis
        if (
            hasattr(analysis_result, "structure_analysis")
            and analysis_result.structure_analysis
        ):
            section_count = len(
                getattr(analysis_result.structure_analysis, "sections", [])
            )
            logger.info(f"Structure analysis found {section_count} sections")
        else:
            logger.warning("No structure analysis available")

        # Check line item analysis
        if (
            hasattr(analysis_result, "line_item_analysis")
            and analysis_result.line_item_analysis
        ):
            item_count = len(getattr(analysis_result.line_item_analysis, "items", []))
            logger.info(f"Line item analysis found {item_count} line items")
        else:
            logger.warning("No line item analysis available")

        # Check validation analysis
        if (
            hasattr(analysis_result, "validation_analysis")
            and analysis_result.validation_analysis
        ):
            validation_details = extract_validation_details(
                analysis_result.validation_analysis
            )
        else:
            logger.warning("No validation analysis available")

        # Save results to file if output directory is provided
        if output_dir:
            try:
                # Create a dictionary with analysis results
                results_dict = {
                    "receipt_id": receipt_id,
                    "image_id": image_id,
                    "timestamp": analysis_result.field_analysis.metadata.get(
                        "created_at", ""
                    ),
                    "field_analysis": {
                        "labeled_fields": len(
                            getattr(analysis_result.field_analysis, "labels", [])
                        ),
                        "fields": [
                            {"text": label.text, "label": label.label}
                            for label in getattr(
                                analysis_result.field_analysis, "labels", []
                            )
                        ],
                    },
                    "structure_analysis": {
                        "sections": [
                            {
                                "name": section.name,
                                "pattern_count": len(section.spatial_patterns),
                            }
                            for section in getattr(
                                analysis_result.structure_analysis, "sections", []
                            )
                        ]
                    },
                    "line_item_analysis": {
                        "item_count": len(
                            getattr(analysis_result.line_item_analysis, "items", [])
                        ),
                        "subtotal": getattr(
                            analysis_result.line_item_analysis, "subtotal", None
                        ),
                        "total": getattr(
                            analysis_result.line_item_analysis, "total", None
                        ),
                        "tax": getattr(analysis_result.line_item_analysis, "tax", None),
                    },
                }

                # Add validation details if available
                if (
                    hasattr(analysis_result, "validation_analysis")
                    and analysis_result.validation_analysis
                ):
                    results_dict["validation_analysis"] = validation_details

                # Save to file
                output_file = (
                    output_dir / f"receipt_analysis_{receipt_id}_{image_id}.json"
                )
                with open(output_file, "w") as f:
                    json.dump(results_dict, f, indent=2)

                logger.info(f"Analysis results saved to {output_file}")

            except Exception as e:
                logger.error(f"Error saving results to file: {str(e)}")

        return True

    except Exception as e:
        logger.error(f"Unexpected error in analyze_single_receipt: {str(e)}")
        logger.error(traceback.format_exc())
        return False


def main():
    """Main function to parse arguments and run the script."""
    parser = argparse.ArgumentParser(
        description="Process a single receipt and save analysis to DynamoDB"
    )
    parser.add_argument("--receipt_id", required=True, help="Receipt ID to process")
    parser.add_argument(
        "--image_id", required=True, help="Image ID containing the receipt"
    )
    parser.add_argument("--output_dir", help="Directory to save JSON output (optional)")

    args = parser.parse_args()

    success = analyze_single_receipt(args.receipt_id, args.image_id, args.output_dir)

    if success:
        logger.info("Receipt processing completed successfully")
        return 0
    else:
        logger.error("Receipt processing failed")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()  # Direct call instead of asyncio.run()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unhandled exception: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
