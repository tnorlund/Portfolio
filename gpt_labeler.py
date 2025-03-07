from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.data.places_api import BatchPlacesProcessor
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
import logging  # Add logging import

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file from root directory
load_dotenv()

def validate_receipt_data(field_analysis, places_api_data, receipt_words, batch_processor):
    """Validate receipt data against Places API and internal consistency."""
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

    # Group labels by their label type
    fields = {}
    for label in field_analysis["labels"]:
        label_type = label["label"]
        if label_type not in fields:
            fields[label_type] = []

        # Get the text for this word
        word_text = word_text_map.get((label["line_id"], label["word_id"]))
        if word_text:
            fields[label_type].append(word_text)

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
        logger.info(f"Comparing normalized addresses:\nReceipt: {receipt_address_norm}\nAPI: {api_address_norm}")
        
        if receipt_address_norm not in api_address_norm and api_address_norm not in receipt_address_norm:
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
                    logger.info(f"Trying date format: {fmt} for date/time: {receipt_date} {receipt_time}")
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
                try:
                    return float(cleaned)
                except ValueError:
                    return None

            subtotal_text = " ".join(fields["subtotal"])
            tax_text = " ".join(fields["tax"])
            total_text = " ".join(fields["total"])

            subtotal = extract_amount(subtotal_text)
            tax = extract_amount(tax_text)
            total = extract_amount(total_text)

            if all(x is not None for x in [subtotal, tax, total]):
                if (
                    abs((subtotal + tax) - total) > 0.01
                ):  # Allow for small rounding differences
                    validation_results["cross_field_consistency"].append(
                        {
                            "type": "error",
                            "message": f"Total mismatch: {subtotal} + {tax} != {total}",
                        }
                    )
            else:
                validation_results["cross_field_consistency"].append(
                    {
                        "type": "warning",
                        "message": f"Could not parse amounts: subtotal={subtotal_text}, tax={tax_text}, total={total_text}",
                    }
                )
        except Exception as e:
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


def main():
    # Load environment and create clients
    env = load_env("dev")
    client = DynamoClient(env["dynamodb_table_name"])
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")  # This will now read from the .env file
    if not api_key:
        raise ValueError("GOOGLE_PLACES_API_KEY not found in .env file")
    batch_processor = BatchPlacesProcessor(api_key, env["dynamodb_table_name"])

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
    # This is the 20th receipt
    last_evaluated_key = {
        "PK": {"S": "IMAGE#1d5ab3e0-7d81-4de4-b23d-1f490f85d89c"},
        "SK": {"S": "RECEIPT#00001"},
        "TYPE": {"S": "RECEIPT"},
    }
    receipts, last_evaluated_key = client.listReceipts(
        limit=20, 
        # lastEvaluatedKey=last_evaluated_key
    )  # Analyze 20 receipt
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
                receipt_lines,
                receipt_words,
                receipt_letters,
                tags,
                validations,
                initial_taggings,
            ) = client.getReceiptDetails(receipt.image_id, receipt.receipt_id)

            # Format receipt for Places API
            receipt_dict = {
                "receipt_id": receipt.receipt_id,
                "image_id": receipt.image_id,
                "words": [
                    {
                        "text": word.text,
                        "extracted_data": (
                            word.extracted_data if word.extracted_data else None
                        ),
                    }
                    for word in receipt_words
                ],
            }

            # Get Places API data
            logger.info("\nCalling Places API...")
            try:
                enriched_receipt = batch_processor.process_receipt_batch([receipt_dict])[0]
                if enriched_receipt.get("places_api_match"):
                    logger.info(f"Found match: {enriched_receipt['places_api_match'].get('name', 'Unknown')} at {enriched_receipt['places_api_match'].get('formatted_address', 'Unknown address')}")
                else:
                    logger.info("No Places API match found")
            except Exception as e:
                logger.error(f"Error calling Places API: {str(e)}")
                logger.error(f"Receipt data: address={receipt_dict.get('address', 'None')}, phone={receipt_dict.get('phone', 'None')}")
                raise

            if "places_api_match" not in enriched_receipt or enriched_receipt["places_api_match"] is None:
                logger.warning(
                    f"No Places API match found for receipt [image_id: {receipt.image_id}]"
                )
                stats["errors"].append(f"No Places API match: {receipt.image_id}")
                continue

            # Step 1: Structure Analysis
            logger.info("Analyzing structure with GPT...")
            try:
                structure_analysis, structure_query, structure_response = (
                    gpt_request_structure_analysis(
                        receipt=receipt_data,
                        receipt_lines=receipt_lines,
                        receipt_words=receipt_words,
                        places_api_data=enriched_receipt["places_api_match"],
                    )
                )
            except Exception as e:
                logger.error(f"Error in structure analysis: {str(e)}")
                raise

            # Update structure statistics
            stats["successful_analysis"] += 1
            stats["avg_confidence"] += structure_analysis["overall_confidence"]

            # Track section types
            for section in structure_analysis["discovered_sections"]:
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

            # Step 2: Field Labeling (Word-Level Tagging)
            logger.info("Performing word-level tagging...")
            try:
                field_analysis, field_query, field_response = (
                    gpt_request_field_labeling(
                        receipt=receipt_data,
                        receipt_lines=receipt_lines,
                        receipt_words=receipt_words,
                        section_boundaries=structure_analysis,
                        places_api_data=enriched_receipt["places_api_match"],
                    )
                )
            except Exception as e:
                logger.error(f"Error in field labeling: {str(e)}")
                raise

            # Update word label statistics
            stats["word_label_stats"]["total_words"] += len(receipt_words)
            stats["word_label_stats"]["labeled_words"] += len(field_analysis["labels"])

            # Track label distribution and confidence
            total_confidence = 0
            for label_info in field_analysis["labels"]:
                label_type = label_info["label"]
                if label_type not in stats["word_label_stats"]["label_distribution"]:
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

            if field_analysis["labels"]:
                stats["word_label_stats"][
                    "avg_label_confidence"
                ] += total_confidence / len(field_analysis["labels"])

            # Add validation step
            try:
                validation_results = validate_receipt_data(
                    field_analysis, 
                    enriched_receipt["places_api_match"], 
                    receipt_words,
                    batch_processor
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
                                stats["validation_results"]["validation_errors"].append(
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
                "receipt_id": receipt.receipt_id,
                "image_id": receipt.image_id,
                "structure_analysis": structure_analysis,
                "field_analysis": field_analysis,
                "places_api_data": enriched_receipt["places_api_match"],
            }

            output_file = (
                output_dir / f"analysis_{receipt.image_id}_{receipt.receipt_id}.json"
            )
            with open(output_file, "w") as f:
                json.dump(result, f, indent=2)

            logger.info(f"\nAnalysis saved to {output_file}")
            logger.info(
                f"Structure confidence: {structure_analysis['overall_confidence']:.2f}"
            )
            logger.info(
                f"Word labeling confidence: {field_analysis['metadata']['average_confidence']:.2f}"
            )

            logger.info("\nDiscovered sections:")
            for section in structure_analysis["discovered_sections"]:
                logger.info(f"\n{section['name'].upper()}:")
                logger.info(f"Confidence: {section['confidence']:.2f}")
                logger.info(f"Line count: {len(section['line_ids'])}")
                logger.info(
                    f"Spatial pattern: {section['spatial_patterns'][0] if section['spatial_patterns'] else 'None'}"
                )
                logger.info(
                    f"Content pattern: {section['content_patterns'][0] if section['content_patterns'] else 'None'}"
                )

            logger.info("\nWord-level tags:")
            current_line = None
            for label in field_analysis["labels"]:
                if label["line_id"] != current_line:
                    current_line = label["line_id"]
                    logger.info(f"\nLine {current_line}:")
                word = next(
                    w
                    for w in receipt_words
                    if w.line_id == label["line_id"] and w.word_id == label["word_id"]
                )
                logger.info(
                    f"  '{word.text}' -> {label['label']} (conf: {label['confidence']:.2f})"
                )

            # Sleep to respect rate limits
            sleep(1)

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
            logger.error(f"Error Type: {type(e).__name__}")
            logger.error(f"Error Message: {str(e)}")
            logger.error("Traceback:")
            logger.error(traceback.format_exc())
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
    logger.info("\n" + "=" * 50)
    logger.info("ANALYSIS SUMMARY")
    logger.info("=" * 50)
    logger.info(f"Total receipts processed: {stats['total_receipts']}")
    logger.info(f"Successful analyses: {stats['successful_analysis']}")
    logger.info(f"Average structure confidence: {stats['avg_confidence']:.2f}")
    logger.info(f"\nWord-Level Tagging:")
    logger.info(f"Total words processed: {stats['word_label_stats']['total_words']}")
    logger.info(f"Words labeled: {stats['word_label_stats']['labeled_words']}")
    logger.info(
        f"Average label confidence: {stats['word_label_stats']['avg_label_confidence']:.2f}"
    )

    logger.info("\nMost common sections:")
    sorted_sections = sorted(
        stats["section_types"].items(), key=lambda x: x[1]["count"], reverse=True
    )
    for section_name, section_stats in sorted_sections:
        logger.info(f"\n{section_name}:")
        logger.info(f"  Count: {section_stats['count']}")
        logger.info(f"  Avg confidence: {section_stats['avg_confidence']:.2f}")
        if section_stats["spatial_patterns"]:
            top_spatial = max(
                section_stats["spatial_patterns"].items(), key=lambda x: x[1]
            )
            logger.info(
                f"  Top spatial pattern: {top_spatial[0]} ({top_spatial[1]} occurrences)"
            )
        if section_stats["content_patterns"]:
            top_content = max(
                section_stats["content_patterns"].items(), key=lambda x: x[1]
            )
            logger.info(
                f"  Top content pattern: {top_content[0]} ({top_content[1]} occurrences)"
            )

    logger.info("\nMost common word labels:")
    sorted_labels = sorted(
        stats["word_label_stats"]["label_distribution"].items(),
        key=lambda x: x[1]["count"],
        reverse=True,
    )
    for label_type, label_stats in sorted_labels:
        logger.info(f"\n{label_type}:")
        logger.info(f"  Count: {label_stats['count']}")
        logger.info(f"  Avg confidence: {label_stats['avg_confidence']:.2f}")

    if stats["errors"]:
        logger.error("\nErrors encountered:")
        for error in stats["errors"]:
            logger.error(f"- Receipt {error['receipt_id']}: {error['error_message']}")

    # Add validation summary to output
    logger.info("\nValidation Summary:")
    logger.info(
        f"Total valid receipts: {stats['validation_results']['total_valid']}/{stats['total_receipts']}"
    )
    if stats["validation_results"]["validation_errors"]:
        logger.error("\nValidation Errors:")
        for error in stats["validation_results"]["validation_errors"]:
            logger.error(f"- Receipt {error['receipt_id']}: {error['message']}")
    if stats["validation_results"]["validation_warnings"]:
        logger.warning("\nValidation Warnings:")
        for warning in stats["validation_results"]["validation_warnings"]:
            logger.warning(f"- Receipt {warning['receipt_id']}: {warning['message']}")


if __name__ == "__main__":
    main()
