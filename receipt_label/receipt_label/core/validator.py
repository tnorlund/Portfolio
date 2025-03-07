from typing import Dict, List, Optional
import re
from datetime import datetime
import logging
from ..data.places_api import BatchPlacesProcessor
from ..utils.address import normalize_address
from ..models.receipt import ReceiptWord

logger = logging.getLogger(__name__)


class ReceiptValidator:
    """Validates receipt data against Places API and internal consistency."""

    def validate_receipt_data(
        self,
        field_analysis: Dict,
        places_api_data: Optional[Dict],
        receipt_words: List[ReceiptWord],
        batch_processor: BatchPlacesProcessor,
    ) -> Dict:
        """Validate receipt data against Places API and internal consistency.

        Args:
            field_analysis: Field analysis results
            places_api_data: Places API data
            receipt_words: List of receipt words
            batch_processor: Places API batch processor

        Returns:
            Dict containing validation results
        """
        validation_results = {
            "business_identity": [],
            "address_verification": [],
            "phone_validation": [],
            "hours_verification": [],
            "cross_field_consistency": [],
            "overall_valid": True,
        }

        # Create a mapping of line_id and word_id to word text
        word_text_map = {
            (word.line_id, word.word_id): word.text for word in receipt_words
        }

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
            api_name = places_api_data.get("name", "") if places_api_data else ""

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
        if "address_line" in fields and places_api_data:
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
        if "phone" in fields and places_api_data:
            receipt_phone = " ".join(fields["phone"])
            api_phone = places_api_data.get("formatted_phone_number")

            # Only compare if we have both phone numbers
            if api_phone:
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
                    "%m/%d/%Y %H:%M:%S",
                    "%m/%d/%Y %H:%M",
                    "%m/%d/%y %H:%M:%S",
                    "%m/%d/%y %I:%M %p",
                    "%A, %B %d, %Y %I:%M %p",
                    "%m/%d/%Y %I:%M %p",
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
                    error_msg = (
                        f"Could not parse date/time: {receipt_date} {receipt_time}"
                    )
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
                    {
                        "type": "error",
                        "message": f"Error processing amounts: {str(e)}",
                    }
                )

        # Set overall validity
        validation_results["overall_valid"] = not any(
            any(item["type"] == "error" for item in group)
            for group in validation_results.values()
            if isinstance(group, list)
        )

        return validation_results
