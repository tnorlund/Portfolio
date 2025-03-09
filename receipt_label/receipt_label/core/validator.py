from typing import Dict, List, Optional
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime
import logging
from ..data.places_api import BatchPlacesProcessor
from ..utils.address import normalize_address
from ..models.receipt import ReceiptWord, Receipt
from ..models.line_item import LineItemAnalysis

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set default level to INFO


class ReceiptValidator:
    """Validates receipt data against Places API and internal consistency."""

    def validate_receipt_data(
        self,
        field_analysis: Dict,
        places_api_data: Optional[Dict],
        receipt_words: List[ReceiptWord],
        line_item_analysis: Optional[LineItemAnalysis],
        batch_processor: BatchPlacesProcessor,
    ) -> Dict:
        """Validate receipt data against Places API and internal consistency.

        Args:
            field_analysis: Field analysis results
            places_api_data: Places API data
            receipt_words: List of receipt words
            line_item_analysis: Line item analysis results
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
            "line_item_validation": [],
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
            receipt_address_norm = normalize_address(receipt_address)
            api_address_norm = normalize_address(api_address)
            logger.debug("Comparing normalized addresses:\nReceipt: %s\nAPI: %s",
                        receipt_address_norm, api_address_norm)

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
                date_formats = [
                    "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M",
                    "%m/%d/%y %I:%M %p", "%m/%d/%y %H:%M %p", "%m/%d/%y %H:%M:%S",
                    "%m/%d/%y %H:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                    "%m/%d/%y %H:%M:%S", "%m/%d/%y %I:%M %p",
                    "%A, %B %d, %Y %I:%M %p", "%m/%d/%Y %I:%M %p",
                ]

                receipt_datetime = None
                for fmt in date_formats:
                    try:
                        logger.debug("Trying date format: %s for date/time: %s %s",
                                   fmt, receipt_date, receipt_time)
                        receipt_datetime = datetime.strptime(f"{receipt_date} {receipt_time}", fmt)
                        logger.debug("Successfully parsed date/time with format: %s", fmt)
                        break
                    except ValueError:
                        continue

                if receipt_datetime is None:
                    error_msg = f"Could not parse date/time: {receipt_date} {receipt_time}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

            except ValueError as e:
                validation_results["hours_verification"].append({
                    "type": "error",
                    "message": f"Invalid date/time format: {receipt_date} {receipt_time}"
                })

        # 5. Cross-field Consistency
        try:
            # Extract numeric values, handling currency symbols and commas
            def extract_amount(text_list):
                if not text_list:
                    return Decimal('0')
                text = " ".join(text_list)
                # Remove currency symbols, commas, and whitespace
                # Extract just the numeric part with optional decimal
                match = re.search(r'(\d+\.?\d*)', text)
                if match:
                    try:
                        return Decimal(match.group(1))
                    except (ValueError, InvalidOperation):
                        logger.warning(f"Could not parse amount: {text}")
                        return None
                return None

            # Get all monetary components
            components = {
                "line_items_total": Decimal('0'),
                "subtotal": extract_amount(fields.get("subtotal", [])),
                "tax": extract_amount(fields.get("tax", [])),
                "total": extract_amount(fields.get("total", [])),
                "fees": extract_amount(fields.get("fee", [])) + extract_amount(fields.get("service_charge", [])),
                "discounts": extract_amount(fields.get("discount", [])),
                "tips": extract_amount(fields.get("tip", [])),
            }

            # Calculate line items total if we have line item analysis
            if line_item_analysis and line_item_analysis.items:
                for item in line_item_analysis.items:
                    if item.price and item.price.extended_price:
                        components["line_items_total"] += item.price.extended_price
                    elif item.price and item.price.unit_price and item.quantity:
                        components["line_items_total"] += item.price.unit_price * item.quantity.amount

            # Log all components for debugging
            logger.debug("Receipt components:")
            for component, value in components.items():
                logger.debug("  %s: %s", component, value)

            # Check for missing total (error)
            if components["total"] is None:
                validation_results["cross_field_consistency"].append({
                    "type": "error",
                    "message": "Missing total amount"
                })

            # Check for missing subtotal and tax (warnings)
            if components["subtotal"] is None:
                validation_results["cross_field_consistency"].append({
                    "type": "warning",
                    "message": "Missing subtotal"
                })

            if components["tax"] is None:
                validation_results["cross_field_consistency"].append({
                    "type": "warning",
                    "message": "Missing tax"
                })

            # Validate line items against subtotal if both are present
            if components["subtotal"] is not None and components["line_items_total"] > 0:
                difference = abs(components["line_items_total"] - components["subtotal"])
                if difference > Decimal('0.02'):  # Allow for rounding differences
                    validation_results["cross_field_consistency"].append({
                        "type": "warning",  # Changed from error to warning
                        "message": f"Line items total ({components['line_items_total']}) doesn't match subtotal ({components['subtotal']})"
                    })
                elif difference > Decimal('0'):
                    validation_results["cross_field_consistency"].append({
                        "type": "info",
                        "message": f"Small rounding difference ({difference}) between line items and subtotal"
                    })

            # Calculate expected total
            if components["subtotal"] is not None:
                expected_total = components["subtotal"]
            else:
                expected_total = components["line_items_total"]

            # Add other components
            if components["tax"] is not None:
                expected_total += components["tax"]
            if components["fees"] is not None:
                expected_total += components["fees"]
            if components["discounts"] is not None:
                expected_total -= abs(components["discounts"])  # Ensure discounts are subtracted
            if components["tips"] is not None:
                expected_total += components["tips"]

            # Compare with actual total if present
            if components["total"] is not None and expected_total > 0:
                difference = abs(expected_total - components["total"])
                if difference > Decimal('0.02'):  # Allow for small rounding differences
                    validation_results["cross_field_consistency"].append({
                        "type": "warning",  # Changed from error to warning
                        "message": (
                            f"Total mismatch: Components sum ({expected_total}) != total ({components['total']})\n"
                            f"Components: {', '.join(f'{k}: {v}' for k, v in components.items() if v != 0)}"
                        )
                    })
                elif difference > Decimal('0'):
                    validation_results["cross_field_consistency"].append({
                        "type": "info",
                        "message": f"Small rounding difference ({difference}) in total calculation"
                    })

        except Exception as e:
            validation_results["cross_field_consistency"].append({
                "type": "error",
                "message": f"Error processing amounts: {str(e)}"
            })

        # New line item validation
        if line_item_analysis:
            self._validate_line_items(validation_results, line_item_analysis, fields)

        # Set overall validity - only fail if there's a missing total or other critical error
        validation_results["overall_valid"] = not any(
            item["type"] == "error" and "Missing total" in item["message"]
            for group in validation_results.values()
            if isinstance(group, list)
            for item in group
        )

        return validation_results

    def _validate_line_items(
        self,
        validation_results: Dict,
        line_item_analysis: LineItemAnalysis,
        fields: Dict,
    ) -> None:
        """Validate line items against receipt totals and internal consistency."""
        
        # Skip if no line items
        if not line_item_analysis.items:
            validation_results["line_item_validation"].append({
                "type": "warning",
                "message": "No line items found in receipt"
            })
            return

        # Calculate total from line items
        line_item_total = Decimal('0')
        for item in line_item_analysis.items:
            if item.price and item.price.extended_price:
                line_item_total += item.price.extended_price
            elif item.price and item.price.unit_price and item.quantity:
                line_item_total += item.price.unit_price * item.quantity.amount

        # Compare with subtotal if available
        if "subtotal" in fields:
            try:
                subtotal = Decimal(re.sub(r'[^\d.]', '', " ".join(fields["subtotal"])))
                difference = abs(subtotal - line_item_total)
                
                if difference > Decimal('0.02'):  # Allow for rounding differences
                    validation_results["line_item_validation"].append({
                        "type": "error",
                        "message": f"Line item total ({line_item_total}) doesn't match subtotal ({subtotal})"
                    })
                elif difference > Decimal('0.00'):
                    validation_results["line_item_validation"].append({
                        "type": "info",
                        "message": f"Small rounding difference ({difference}) between line items and subtotal"
                    })
            except (ValueError, InvalidOperation):
                validation_results["line_item_validation"].append({
                    "type": "warning",
                    "message": "Could not parse subtotal for comparison"
                })

        # Validate individual line items
        for item in line_item_analysis.items:
            # Check for missing required fields
            if not item.price:
                validation_results["line_item_validation"].append({
                    "type": "warning",
                    "message": f"Missing price for item: {item.description}"
                })
            
            # Validate price calculations
            if item.price and item.price.unit_price and item.quantity:
                expected = item.price.unit_price * item.quantity.amount
                if item.price.extended_price and abs(expected - item.price.extended_price) > Decimal('0.01'):
                    validation_results["line_item_validation"].append({
                        "type": "error",
                        "message": (
                            f"Price calculation error for {item.description}: "
                            f"{item.price.unit_price} × {item.quantity.amount} ≠ {item.price.extended_price}"
                        )
                    })

            # Check confidence
            if item.confidence < 0.8:
                validation_results["line_item_validation"].append({
                    "type": "warning",
                    "message": f"Low confidence ({item.confidence}) for item: {item.description}"
                })

def validate_receipt_components(
    receipt: Receipt,
    line_item_analysis: LineItemAnalysis,
) -> bool:
    """Validate receipt components for consistency."""
    # Extract components
    components = {
        "line_items_total": sum(item.price.extended_price for item in line_item_analysis.items),
        "subtotal": line_item_analysis.subtotal or Decimal('0'),
        "tax": line_item_analysis.tax or Decimal('0'),
        "total": line_item_analysis.total or Decimal('0'),
        "fees": Decimal('0'),  # TODO: Add fees to LineItemAnalysis
        "discounts": Decimal('0'),  # TODO: Add discounts to LineItemAnalysis
        "tips": Decimal('0'),  # TODO: Add tips to LineItemAnalysis
    }

    # Validate components
    is_valid = True

    # Check if line items total matches subtotal
    if components["line_items_total"] != components["subtotal"]:
        logger.warning(
            "Line items total (%s) does not match subtotal (%s)",
            components["line_items_total"],
            components["subtotal"]
        )
        is_valid = False

    # Check if total matches subtotal + tax + fees - discounts + tips
    expected_total = (
        components["subtotal"]
        + components["tax"]
        + components["fees"]
        - components["discounts"]
        + components["tips"]
    )
    if components["total"] != expected_total:
        logger.warning(
            "Total (%s) does not match expected total (%s)",
            components["total"],
            expected_total
        )
        is_valid = False

    return is_valid
