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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file from root directory
load_dotenv()


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


def validate_receipt_data(
    field_analysis, places_api_data, receipt_words, batch_processor
):
    """Validate receipt data against Places API and internal consistency."""
    # Configure logging for this function
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    validation_results = {
        "business_identity": [],
        "address_verification": [],
        "phone_validation": [],
        "hours_verification": [],
        "cross_field_consistency": [],
        "overall_valid": True,
    }

    # Create a mapping of line_id and word_id to word text
    word_text_map = {(word.line_id, word.word_id): word.text for word in receipt_words}
    logger.debug("Created word_text_map with %d entries", len(word_text_map))

    # Group labels by their label type and track their line positions
    fields = {}
    field_positions = {}  # Track the line_ids for each field type
    logger.debug("Starting to group fields from %d labels", len(field_analysis["labels"]))
    for label in field_analysis["labels"]:
        label_type = label["label"]
        if label_type not in fields:
            fields[label_type] = []
            field_positions[label_type] = []
            logger.debug("Created new field group for '%s'", label_type)

        # Get the text for this word
        word_text = word_text_map.get((label["line_id"], label["word_id"]))
        if word_text:
            fields[label_type].append(word_text)
            field_positions[label_type].append(label["line_id"])
            logger.debug("Added text '%s' to field '%s' (line_id: %d, word_id: %d)", 
                       word_text, label_type, label["line_id"], label["word_id"])

    logger.debug("Completed field grouping. Fields found: %s", list(fields.keys()))
    for field_type, texts in fields.items():
        logger.debug("Field '%s' contains: %s", field_type, texts)
        if field_positions[field_type]:
            logger.debug("Field '%s' appears on lines: %s", field_type, field_positions[field_type])

    # Check total appears after subtotal
    if "subtotal" in field_positions and "total" in field_positions:
        subtotal_lines = field_positions["subtotal"]
        total_lines = field_positions["total"]
        
        # Get the last occurrence of each
        last_subtotal_line = max(subtotal_lines)
        last_total_line = max(total_lines)
        
        logger.debug("Last subtotal appears on line %d, last total on line %d", 
                    last_subtotal_line, last_total_line)
        
        if last_total_line <= last_subtotal_line:
            validation_results["cross_field_consistency"].append({
                "type": "error",
                "message": f"Total (line {last_total_line}) appears before or at the same line as subtotal (line {last_subtotal_line})"
            })
            logger.warning("Invalid order: Total appears before or at the same line as subtotal")

    # 1. Business Identity Validation
    if "business_name" in fields:
        receipt_name = " ".join(fields["business_name"])
        api_name = places_api_data.get("name", "")

        # Use Places API validation
        is_valid, message, confidence = batch_processor._validate_business_name(
            receipt_name, api_name
        )
        if not is_valid:
            validation_results["business_identity"].append(
                {
                    "type": "warning",
                    "message": message,
                }
            )

    # 2. Address Verification
    if "address_line" in fields:
        receipt_address = " ".join(fields["address_line"])
        api_address = places_api_data.get("formatted_address", "")

        # Normalize addresses for comparison
        receipt_address_norm = normalize_address(receipt_address)
        api_address_norm = normalize_address(api_address)
        logger.info(
            f"Comparing normalized addresses:\nReceipt: {receipt_address_norm}\nAPI: {api_address_norm}"
        )

        if (
            receipt_address_norm not in api_address_norm
            and api_address_norm not in receipt_address_norm
        ):
            validation_results["address_verification"].append(
                {
                    "type": "warning",
                    "message": f"Address mismatch: Receipt '{receipt_address}' vs API '{api_address}'",
                }
            )

    # 3. Phone Validation
    if "phone" in fields:
        receipt_phone = " ".join(fields["phone"])
        api_phone = places_api_data.get("formatted_phone_number")

        # Only compare if we have both phone numbers
        if api_phone:  # Only proceed if API returned a phone number
            # Remove non-numeric characters for comparison
            receipt_clean = re.sub(r"\D", "", receipt_phone)
            api_clean = re.sub(r"\D", "", api_phone)
            if receipt_clean != api_clean:
                validation_results["phone_validation"].append(
                    {
                        "type": "warning",
                        "message": f"Phone number mismatch: Receipt '{receipt_phone}' vs API '{api_phone}'",
                    }
                )
        else:
            validation_results["phone_validation"].append(
                {
                    "type": "info",
                    "message": f"Phone number found on receipt '{receipt_phone}' but not in Places API data",
                }
            )

    # 4. Hours Verification
    if "date" in fields and "time" in fields:
        receipt_date = " ".join(fields["date"])
        receipt_time = " ".join(fields["time"])
        try:
            # Try multiple date formats
            date_formats = [
                "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M",
                "%m/%d/%y %H:%M",
                "%m/%d/%y %I:%M %p",
                "%m/%d/%y %H:%M %p",
                "%m/%d/%y %H:%M:%S",
                "%m/%d/%y %H:%M:%S %p",
                "%m/%d/%Y %H:%M:%S",  # Added for format like "05/17/2024 15:46:28"
                "%m/%d/%Y %H:%M",  # Added for format like "06/27/2024 16:00"
                "%m/%d/%y %H:%M:%S",  # Added for format like "11/11/24 11:46"
                "%m/%d/%y %I:%M %p",  # Added for format like "01/15/25 12:07 PM"
                "%A, %B %d, %Y %I:%M %p",  # Added for format like "Wednesday, December 4, 2024 03:58 PM"
                "%m/%d/%Y %I:%M %p",  # Added for format like "MM/DD/YYYY HH:MM AM/PM" (e.g., "04/30/2024 08:29 PM")
            ]

            receipt_datetime = None
            for fmt in date_formats:
                try:
                    logger.info(
                        f"Trying date format: {fmt} for date/time: {receipt_date} {receipt_time}"
                    )
                    receipt_datetime = datetime.strptime(
                        f"{receipt_date} {receipt_time}", fmt
                    )
                    logger.info(f"Successfully parsed date/time with format: {fmt}")
                    break
                except ValueError:
                    logger.debug(f"Failed to parse with format: {fmt}")
                    continue

            if receipt_datetime is None:
                error_msg = f"Could not parse date/time: {receipt_date} {receipt_time}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            # TODO: Add business hours verification when available in Places API
            pass
        except ValueError as e:
            validation_results["hours_verification"].append(
                {
                    "type": "error",
                    "message": f"Invalid date/time format: {receipt_date} {receipt_time}",
                }
            )

    # 5. Cross-field Consistency
    if all(k in fields for k in ["subtotal", "tax", "total"]):
        try:
            # Extract numeric values, handling currency symbols and commas
            def extract_amount(text):
                # Remove currency symbols, commas, and whitespace
                cleaned = re.sub(r"[$,]", "", text)
                logger.debug("Attempting to extract amount from text: '%s' (cleaned: '%s')", text, cleaned)
                try:
                    amount = float(cleaned)
                    logger.debug("Successfully extracted amount: %f", amount)
                    return amount
                except ValueError as e:
                    logger.debug("Failed to extract amount: %s", str(e))
                    return None

            subtotal_text = " ".join(fields["subtotal"])
            tax_text = " ".join(fields["tax"])
            total_text = " ".join(fields["total"])

            logger.debug("Processing amounts - subtotal: '%s', tax: '%s', total: '%s'", 
                        subtotal_text, tax_text, total_text)

            subtotal = extract_amount(subtotal_text)
            tax = extract_amount(tax_text)
            total = extract_amount(total_text)

            logger.debug("Extracted amounts - subtotal: %s, tax: %s, total: %s", 
                        subtotal, tax, total)

            if all(x is not None for x in [subtotal, tax, total]):
                if abs((subtotal + tax) - total) > 0.01:  # Allow for small rounding differences
                    validation_results["cross_field_consistency"].append({
                        "type": "error",
                        "message": f"Total mismatch: {subtotal} + {tax} != {total}",
                    })
            else:
                validation_results["cross_field_consistency"].append({
                    "type": "warning",
                    "message": f"Could not parse amounts: subtotal={subtotal_text}, tax={tax_text}, total={total_text}",
                })
        except Exception as e:
            logger.error("Error processing amounts: %s", str(e))
            validation_results["cross_field_consistency"].append(
                {"type": "error", "message": f"Error processing amounts: {str(e)}"}
            )

    # Set overall validity
    validation_results["overall_valid"] = not any(
        any(item["type"] == "error" for item in group)
        for group in validation_results.values()
        if isinstance(group, list)
    )

    return validation_results


async def process_receipt(labeler, receipt_obj, receipt_words, receipt_lines):
    """Process a single receipt asynchronously."""
    return await labeler.label_receipt(receipt_obj, receipt_words, receipt_lines)


async def main():
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

        # Initialize the ReceiptLabeler with validated API keys
        labeler = ReceiptLabeler(
            places_api_key=os.getenv("GOOGLE_PLACES_API_KEY"),
            dynamodb_table_name=env["dynamodb_table_name"],
            gpt_api_key=os.getenv("OPENAI_API_KEY"),
        )

        # Create output directory
        output_dir = Path("analysis_results")
        output_dir.mkdir(exist_ok=True)

        # Track statistics
        stats = {
            "total_receipts": 0,
            "successful_analysis": 0,
            "section_types": {},
            "avg_confidence": 0.0,
            "word_label_stats": {
                "total_words": 0,
                "labeled_words": 0,
                "label_distribution": {},
                "avg_label_confidence": 0.0,
            },
            "errors": [],
            "validation_results": {
                "total_valid": 0,
                "validation_errors": [],
                "validation_warnings": [],
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
            limit=30, lastEvaluatedKey=last_evaluated_key
        )  # Analyze 30 receipts
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
                    tags,
                    validations,
                    initial_taggings,
                ) = client.getReceiptDetails(receipt.image_id, receipt.receipt_id)

                # Convert DynamoDB objects to receipt_label objects
                try:
                    # Convert DynamoReceiptWord and DynamoReceiptLine objects using from_dynamo
                    receipt_words = [ReceiptWord.from_dynamo(word) for word in receipt_words_data]
                    receipt_lines = [ReceiptLine.from_dynamo(line) for line in receipt_lines_data]

                    # Create Receipt object from DynamoDB data
                    receipt_obj = Receipt.from_dynamo(
                        receipt.receipt_id, receipt.image_id, receipt_words, receipt_lines
                    )
                except Exception as e:
                    logger.error(f"Error converting data to objects: {str(e)}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    raise

                # Analyze receipt using ReceiptLabeler
                analysis_result = await process_receipt(labeler, receipt_obj, receipt_words, receipt_lines)

                # Update statistics
                stats["successful_analysis"] += 1
                stats["avg_confidence"] += analysis_result.structure_analysis[
                    "overall_confidence"
                ]

                # Track section types
                for section in analysis_result.structure_analysis[
                    "discovered_sections"
                ]:
                    section_name = section["name"]
                    if section_name not in stats["section_types"]:
                        stats["section_types"][section_name] = {
                            "count": 0,
                            "avg_confidence": 0.0,
                            "spatial_patterns": {},
                            "content_patterns": {},
                        }

                    section_stats = stats["section_types"][section_name]
                    section_stats["count"] += 1
                    section_stats["avg_confidence"] += section["confidence"]

                    # Track patterns
                    for pattern in section["spatial_patterns"]:
                        section_stats["spatial_patterns"][pattern] = (
                            section_stats["spatial_patterns"].get(pattern, 0) + 1
                        )
                    for pattern in section["content_patterns"]:
                        section_stats["content_patterns"][pattern] = (
                            section_stats["content_patterns"].get(pattern, 0) + 1
                        )

                # Update word label statistics
                stats["word_label_stats"]["total_words"] += len(receipt_words)
                stats["word_label_stats"]["labeled_words"] += len(
                    analysis_result.field_analysis["labels"]
                )

                # Track label distribution and confidence
                total_confidence = 0
                for label_info in analysis_result.field_analysis["labels"]:
                    label_type = label_info["label"]
                    if (
                        label_type
                        not in stats["word_label_stats"]["label_distribution"]
                    ):
                        stats["word_label_stats"]["label_distribution"][label_type] = {
                            "count": 0,
                            "avg_confidence": 0.0,
                        }

                    label_stats = stats["word_label_stats"]["label_distribution"][
                        label_type
                    ]
                    label_stats["count"] += 1
                    label_stats["avg_confidence"] += label_info["confidence"]
                    total_confidence += label_info["confidence"]

                if analysis_result.field_analysis["labels"]:
                    stats["word_label_stats"][
                        "avg_label_confidence"
                    ] += total_confidence / len(
                        analysis_result.field_analysis["labels"]
                    )

                # Add validation step
                try:
                    validation_results = validate_receipt_data(
                        analysis_result.field_analysis,
                        analysis_result.places_api_data,
                        receipt_words,
                        labeler.places_processor,
                    )
                except Exception as e:
                    logger.error(f"Error in validation: {str(e)}")
                    raise

                # Update validation statistics
                if validation_results["overall_valid"]:
                    stats["validation_results"]["total_valid"] += 1
                else:
                    for category, results in validation_results.items():
                        if isinstance(results, list):
                            for result in results:
                                if result["type"] == "error":
                                    stats["validation_results"][
                                        "validation_errors"
                                    ].append(
                                        {
                                            "receipt_id": receipt.receipt_id,
                                            "category": category,
                                            "message": result["message"],
                                        }
                                    )
                                elif result["type"] == "warning":
                                    stats["validation_results"][
                                        "validation_warnings"
                                    ].append(
                                        {
                                            "receipt_id": receipt.receipt_id,
                                            "category": category,
                                            "message": result["message"],
                                        }
                                    )

                # Save analysis results
                result = {
                    "receipt_id": analysis_result.receipt_id,
                    "image_id": analysis_result.image_id,
                    "structure_analysis": analysis_result.structure_analysis,
                    "field_analysis": analysis_result.field_analysis,
                    "places_api_data": analysis_result.places_api_data,
                    "validation_results": analysis_result.validation_results,
                    "overall_confidence": analysis_result.structure_analysis[
                        "overall_confidence"
                    ],
                }
                output_file = (
                    output_dir
                    / f"analysis_{receipt.image_id}_{receipt.receipt_id}.json"
                )
                with open(output_file, "w") as f:
                    json.dump(result, f, indent=2)

                logger.info(f"\nAnalysis saved to {output_file}")
                logger.info(
                    f"Structure confidence: {analysis_result.structure_analysis['overall_confidence']:.2f}"
                )
                logger.info(
                    f"Word labeling confidence: {analysis_result.field_analysis['metadata']['average_confidence']:.2f}"
                )

                # Update confidence totals
                total_structure_confidence += analysis_result.structure_analysis[
                    "overall_confidence"
                ]
                total_word_confidence += analysis_result.field_analysis["metadata"][
                    "average_confidence"
                ]

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
                continue

        # Calculate final statistics
        if stats["successful_analysis"] > 0:
            stats["avg_confidence"] /= stats["successful_analysis"]
            for section_name, section_stats in stats["section_types"].items():
                section_stats["avg_confidence"] /= section_stats["count"]

            if stats["word_label_stats"]["labeled_words"] > 0:
                stats["word_label_stats"]["avg_label_confidence"] /= stats[
                    "successful_analysis"
                ]
                for label_type, label_stats in stats["word_label_stats"][
                    "label_distribution"
                ].items():
                    label_stats["avg_confidence"] /= label_stats["count"]

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
        logger.info("Average structure confidence: %.2f", stats["avg_confidence"])
        logger.info("\nWord-Level Statistics:")
        logger.info(
            "Total words processed: %d", stats["word_label_stats"]["total_words"]
        )
        logger.info("Words labeled: %d", stats["word_label_stats"]["labeled_words"])
        logger.info(
            "Average label confidence: %.2f",
            stats["word_label_stats"]["avg_label_confidence"],
        )

        # Print validation summary
        logger.info("\nValidation Summary:")
        logger.info(
            "Total valid receipts: %d/%d",
            stats["validation_results"]["total_valid"],
            stats["total_receipts"],
        )

        if stats["validation_results"]["validation_errors"]:
            logger.error("\nValidation Errors:")
            for error in stats["validation_results"]["validation_errors"]:
                logger.error("- Receipt %s: %s", error["receipt_id"], error["message"])

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
