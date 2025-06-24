import asyncio
import logging
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from receipt_dynamo.data.dynamo_client import DynamoClient

from ..data.places_api import BatchPlacesProcessor
from ..models.label import LabelAnalysis, WordLabel
from ..models.line_item import LineItem, LineItemAnalysis
from ..models.receipt import Receipt, ReceiptLine, ReceiptWord
from ..models.structure import StructureAnalysis
from ..models.validation import (
    ValidationAnalysis,
    ValidationResult,
    ValidationResultType,
    ValidationStatus,
)
from ..processors.line_item_processor import LineItemProcessor
from ..processors.receipt_analyzer import ReceiptAnalyzer
from ..utils import get_package_version

logger = logging.getLogger(__name__)


class LabelingResult:
    """Class to encapsulate results from the receipt labeling process.

    This class holds all the outputs from the receipt labeling pipeline, including
    structure analysis, field labeling, line item analysis, and validation results.
    """

    def __init__(
        self,
        receipt_id: str,
        structure_analysis: StructureAnalysis = None,
        field_analysis: LabelAnalysis = None,
        line_item_analysis: Optional[LineItemAnalysis] = None,
        validation_analysis: ValidationAnalysis = None,
        places_api_data: Dict = None,
        execution_times: Dict = None,
        errors: Dict = None,
    ):
        """Initialize LabelingResult object.

        Args:
            receipt_id: Unique identifier for the receipt
            structure_analysis: Results from structure analysis
            field_analysis: Results from field labeling
            line_item_analysis: Analyzed line items
            validation_analysis: Results from validation checks
            places_api_data: Data retrieved from Places API
            execution_times: Dictionary of execution times for different steps
            errors: Dictionary of errors encountered during processing
        """
        self.receipt_id = receipt_id
        self.structure_analysis = structure_analysis
        self.field_analysis = field_analysis
        self.line_item_analysis = line_item_analysis
        self.validation_analysis = validation_analysis
        self.places_api_data = places_api_data
        self.execution_times = execution_times if execution_times else {}
        self.errors = errors if errors else {}

    def to_dict(self) -> Dict:
        """Convert LabelingResult to dictionary format.

        Returns:
            Dictionary representation of LabelingResult
        """
        return {
            "receipt_id": self.receipt_id,
            "structure_analysis": (
                self.structure_analysis.to_dict()
                if self.structure_analysis
                else None
            ),
            "field_analysis": (
                self.field_analysis.to_dict() if self.field_analysis else None
            ),
            "line_item_analysis": (
                self.line_item_analysis.to_dict()
                if self.line_item_analysis
                else None
            ),
            "validation_analysis": (
                self.validation_analysis.to_dict()
                if self.validation_analysis
                else None
            ),
            "places_api_data": self.places_api_data,
            "execution_times": self.execution_times,
            "errors": self.errors,
        }


class ReceiptLabeler:
    """Main class for receipt labeling."""

    def __init__(
        self,
        places_api_key: Optional[str] = None,
        gpt_api_key: Optional[str] = None,
        dynamodb_table_name: Optional[str] = None,
        validation_level: str = "basic",
        validation_config: Optional[Dict] = None,
    ):
        """Initialize the labeler with optional API keys and validation configuration.

        Args:
            places_api_key: Google Places API key for business validation
            gpt_api_key: OpenAI API key for GPT-based processing
            dynamodb_table_name: DynamoDB table name for caching
            validation_level: Level of validation to perform ("basic", "strict", or "none")
                - "basic": Standard validation requiring total, date, and business name
                - "strict": Stricter validation requiring all fields to match exactly
                - "none": No validation is performed
            validation_config: [Deprecated] Use validation_level instead.
                Configuration options will be automatically set based on the validation_level.
        """
        # For backward compatibility, if dynamodb_table_name is provided, 
        # we'll set the environment variable
        if dynamodb_table_name:
            import os
            os.environ["DYNAMO_TABLE_NAME"] = dynamodb_table_name
        
        self.places_processor = BatchPlacesProcessor(
            api_key=places_api_key,
        )
        self.receipt_analyzer = ReceiptAnalyzer(api_key=gpt_api_key or "")
        self.line_item_processor = LineItemProcessor(gpt_api_key=gpt_api_key or "")

        # Store the DynamoDB table name for later use
        self.dynamodb_table_name = dynamodb_table_name

        # Set default validation config based on validation level
        self.validation_config = self._get_validation_config_from_level(
            validation_level
        )

        # For backward compatibility, allow overriding with explicit validation_config
        if validation_config:
            self.validation_config.update(validation_config)

        logger.info(
            f"Initialized ReceiptLabeler with validation_config: {self.validation_config}"
        )

    def label_receipt(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        enable_validation: Optional[bool] = None,
        enable_places_api: bool = True,
    ) -> LabelingResult:
        """Label a receipt using various processors.

        Args:
            receipt: Receipt object containing metadata
            receipt_words: List of ReceiptWord objects
            receipt_lines: List of ReceiptLine objects
            enable_validation: Whether to perform validation checks (overrides config)
            enable_places_api: Whether to use Places API for business identification

        Returns:
            LabelingResult object containing analysis results
        """
        # Use validation config if enable_validation is not explicitly provided
        if enable_validation is None:
            enable_validation = self.validation_config["enable_validation"]

        logger.info(f"Processing receipt {receipt.receipt_id}...")

        # Initialize tracking dictionaries
        execution_times = {}
        errors = {}

        try:
            # Get Places API data if enabled
            places_data = None
            if enable_places_api:
                start_time = time.time()
                places_data = self._get_places_data(receipt_words)
                execution_times["places_api"] = time.time() - start_time

            # Analyze receipt structure
            logger.debug("Starting structure analysis...")
            structure_analysis = self.receipt_analyzer.analyze_structure(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                places_api_data=places_data,
            )
            logger.debug("Structure analysis completed successfully")
            logger.debug(
                f"Structure analysis sections: {len(structure_analysis.sections)} found"
            )

            # Label fields
            logger.debug("Starting field labeling...")
            field_analysis = self.receipt_analyzer.label_fields(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                section_boundaries=structure_analysis,
                places_api_data=places_data,
            )
            logger.debug("Field labeling completed successfully")
            logger.debug(
                f"Field analysis found {len(field_analysis.labels) if hasattr(field_analysis, 'labels') else 0} labels"
            )
            if hasattr(field_analysis, "metadata") and field_analysis.metadata:
                logger.debug(
                    f"Field analysis has metadata: {field_analysis.metadata}"
                )

            # Process line items using line item processor
            logger.info("Processing line items")
            start_time = time.time()
            line_item_analysis = self.line_item_processor.analyze_line_items(
                receipt,
                receipt_lines,
                receipt_words,
                places_data,
                structure_analysis,
            )
            execution_times["line_item_processing"] = time.time() - start_time

            if line_item_analysis:
                logger.info(
                    f"Successfully processed {len(line_item_analysis.items)} line items"
                )

                # Apply line item word labels to field analysis if available
                if (
                    hasattr(line_item_analysis, "word_labels")
                    and line_item_analysis.word_labels
                ):
                    logger.info(
                        f"Applying {len(line_item_analysis.word_labels)} line item word labels"
                    )

                    # Create a pretty logging summary
                    applied_labels = {}
                    updated_labels = {}
                    skipped_labels = {}

                    # Priority mapping - higher priority labels will replace lower priority ones
                    label_priority = {
                        # Line item component labels (high priority)
                        "ITEM_NAME": 8,
                        "ITEM_QUANTITY": 8,
                        "ITEM_UNIT": 8,
                        "ITEM_PRICE": 8,
                        "ITEM_TOTAL": 8,
                        "SUBTOTAL": 9,
                        "TAX": 9,
                        "TOTAL": 9,
                        # Generic field labels (medium priority)
                        "MERCHANT": 5,
                        "ADDRESS": 5,
                        "DATE": 6,
                        "TIME": 6,
                        "ITEM": 4,
                        # Other labels (low priority)
                        "PHONE": 3,
                        "RECEIPT_ID": 3,
                        "CASHIER": 2,
                        "PAYMENT": 3,
                        "MISC": 1,
                    }

                    # Create new labels from line item word labels
                    for (
                        line_id,
                        word_id,
                    ), label_info in line_item_analysis.word_labels.items():
                        # Find the corresponding word
                        for word in receipt_words:
                            if (
                                word.line_id == line_id
                                and word.word_id == word_id
                            ):
                                # Check if this word already has a label
                                existing_label = next(
                                    (
                                        label
                                        for label in field_analysis.labels
                                        if label.line_id == line_id
                                        and label.word_id == word_id
                                    ),
                                    None,
                                )

                                if not existing_label:
                                    # Add new label if no existing label
                                    field_analysis.labels.append(
                                        WordLabel(
                                            text=word.text,
                                            label=label_info["label"],
                                            line_id=line_id,
                                            word_id=word_id,
                                            reasoning=label_info["reasoning"],
                                            bounding_box=(
                                                word.bounding_box
                                                if hasattr(
                                                    word, "bounding_box"
                                                )
                                                else None
                                            ),
                                        )
                                    )
                                    # Add to applied labels for logging
                                    if (
                                        label_info["label"]
                                        not in applied_labels
                                    ):
                                        applied_labels[label_info["label"]] = (
                                            []
                                        )
                                    applied_labels[label_info["label"]].append(
                                        {
                                            "text": word.text,
                                            "position": f"L{line_id}W{word_id}",
                                        }
                                    )
                                    logger.debug(
                                        f"Added new label '{label_info['label']}' to word '{word.text}'"
                                    )
                                else:
                                    # Decide whether to update the existing label
                                    new_label = label_info["label"]
                                    old_label = existing_label.label

                                    # Get priorities (default to 0 if not found) - use uppercase for case-insensitive comparison
                                    new_priority = label_priority.get(
                                        new_label.upper(), 0
                                    )
                                    old_priority = label_priority.get(
                                        old_label.upper(), 0
                                    )

                                    # Update if the new label has higher priority OR if they're the same label type (case-insensitive match)
                                    # but the new one is from the line item processor (uppercase)
                                    if new_priority > old_priority or (
                                        new_label.upper() == old_label.upper()
                                        and new_label.isupper()
                                    ):
                                        # Keep track of the old label for reference
                                        old_reasoning = (
                                            existing_label.reasoning
                                        )

                                        # Update the existing label
                                        existing_label.label = new_label
                                        existing_label.reasoning = f"{label_info['reasoning']} (Previously labeled as '{old_label}': {old_reasoning})"

                                        # Track for logging
                                        if new_label not in updated_labels:
                                            updated_labels[new_label] = []
                                        updated_labels[new_label].append(
                                            {
                                                "text": word.text,
                                                "position": f"L{line_id}W{word_id}",
                                                "previous_label": old_label,
                                            }
                                        )
                                        logger.debug(
                                            f"Updated label from '{old_label}' to '{new_label}' for word '{word.text}'"
                                        )
                                    else:
                                        # If we're not updating, still enhance the reasoning if possible
                                        if "item_index" in label_info:
                                            # Enhance the existing label with line item context
                                            item_idx = label_info["item_index"]
                                            if (
                                                item_idx is not None
                                                and 0
                                                <= item_idx
                                                < len(line_item_analysis.items)
                                            ):
                                                item = (
                                                    line_item_analysis.items[
                                                        item_idx
                                                    ]
                                                )
                                                existing_label.reasoning += f" (Part of line item: {item.description})"

                                        # Track skipped label
                                        if (
                                            label_info["label"]
                                            not in skipped_labels
                                        ):
                                            skipped_labels[
                                                label_info["label"]
                                            ] = []
                                        skipped_labels[
                                            label_info["label"]
                                        ].append(
                                            {
                                                "text": word.text,
                                                "position": f"L{line_id}W{word_id}",
                                                "existing_label": existing_label.label,
                                            }
                                        )
                                        logger.debug(
                                            f"Kept existing label '{existing_label.label}' instead of '{label_info['label']}' for word '{word.text}'"
                                        )

                                break

                    # Print pretty log summary of applied and skipped labels
                    self._log_label_application_summary(
                        applied_labels, updated_labels, skipped_labels
                    )

                    # Update total labeled words count
                    field_analysis.total_labeled_words = len(
                        field_analysis.labels
                    )

                    # Add line item labeling to metadata
                    if "processing_metrics" not in field_analysis.metadata:
                        field_analysis.metadata["processing_metrics"] = {}
                    field_analysis.metadata["processing_metrics"][
                        "line_item_labels"
                    ] = {
                        "count": len(line_item_analysis.word_labels),
                        "applied": sum(
                            len(labels) for labels in applied_labels.values()
                        ),
                        "updated": sum(
                            len(labels) for labels in updated_labels.values()
                        ),
                        "skipped": sum(
                            len(labels) for labels in skipped_labels.values()
                        ),
                        "label_types": {
                            label: sum(
                                1
                                for info in line_item_analysis.word_labels.values()
                                if info.get("label") == label
                            )
                            for label in set(
                                info.get("label")
                                for info in line_item_analysis.word_labels.values()
                            )
                        },
                    }

                    # Add an event to the history
                    field_analysis.add_history_event(
                        "line_item_labels_applied",
                        {
                            "count": len(line_item_analysis.word_labels),
                            "applied": sum(
                                len(labels)
                                for labels in applied_labels.values()
                            ),
                            "updated": sum(
                                len(labels)
                                for labels in updated_labels.values()
                            ),
                            "skipped": sum(
                                len(labels)
                                for labels in skipped_labels.values()
                            ),
                            "timestamp": datetime.now().isoformat(),
                        },
                    )
                else:
                    # Check if we have a valid line_item_analysis object but no word labels
                    if (
                        line_item_analysis
                        and hasattr(line_item_analysis, "items")
                        and line_item_analysis.items
                    ):
                        logger.info(
                            f"Line item analysis found {len(line_item_analysis.items)} items but no word labels"
                        )
                    else:
                        logger.warning(
                            "Line item analysis returned None or has no items"
                        )

            # Create validation results if validation is enabled
            validation_analysis = None
            if enable_validation and line_item_analysis:
                logger.info(
                    f"Performing validation for receipt {receipt.receipt_id}"
                )
                try:
                    # Extract validation-related data from line items and field analysis
                    receipt_total = None
                    receipt_subtotal = None
                    receipt_tax = None
                    receipt_date = None
                    business_name = None

                    # Extract financial values and required fields from field analysis
                    for label in field_analysis.labels:
                        if hasattr(label, "label") and hasattr(label, "text"):
                            # Extract financial values
                            if label.label in ["TOTAL", "Total", "total"]:
                                try:
                                    text_value = label.text.replace(
                                        "$", ""
                                    ).replace(",", "")
                                    receipt_total = float(text_value)
                                except (ValueError, TypeError):
                                    logger.warning(
                                        f"Could not convert {label.label} label text '{label.text}' to float"
                                    )
                            # Check for subtotal
                            elif label.label in [
                                "SUBTOTAL",
                                "Subtotal",
                                "subtotal",
                            ]:
                                try:
                                    text_value = label.text.replace(
                                        "$", ""
                                    ).replace(",", "")
                                    receipt_subtotal = float(text_value)
                                except (ValueError, TypeError):
                                    logger.warning(
                                        f"Could not convert {label.label} label text '{label.text}' to float"
                                    )
                            # Check for tax
                            elif label.label in ["TAX", "Tax", "tax"]:
                                try:
                                    text_value = label.text.replace(
                                        "$", ""
                                    ).replace(",", "")
                                    receipt_tax = float(text_value)
                                except (ValueError, TypeError):
                                    logger.warning(
                                        f"Could not convert {label.label} label text '{label.text}' to float"
                                    )
                            # Check for date
                            elif label.label in ["DATE", "Date", "date"]:
                                receipt_date = label.text
                            # Check for business name
                            elif label.label in [
                                "MERCHANT",
                                "Merchant",
                                "merchant",
                                "BUSINESS_NAME",
                                "business_name",
                            ]:
                                business_name = label.text

                    # Get values from line item analysis, only if use_inferred_values is True
                    use_inferred = self.validation_config.get(
                        "use_inferred_values", True
                    )

                    # For line item totals, only use inferred values if use_inferred_values is True
                    line_item_total = None
                    line_item_subtotal = None
                    line_item_tax = None

                    if use_inferred:
                        line_item_total = (
                            float(line_item_analysis.total)
                            if line_item_analysis.total
                            else None
                        )
                        line_item_subtotal = (
                            float(line_item_analysis.subtotal)
                            if line_item_analysis.subtotal
                            else None
                        )
                        line_item_tax = (
                            float(line_item_analysis.tax)
                            if line_item_analysis.tax
                            else None
                        )

                        # Log that we're using inferred values
                        logger.info(
                            "Using inferred values from line item analysis for validation"
                        )
                    else:
                        logger.info(
                            "Using only explicitly labeled values for validation (inferred values disabled)"
                        )
                        # If there's no explicitly labeled receipt total, subtotal, or tax,
                        # we'll leave them as None and the validation will handle them accordingly

                    # Log the extracted values for debugging
                    logger.info("Financial values for validation:")
                    logger.info(
                        f"  Receipt total: {receipt_total}, Line item total: {line_item_total}"
                    )
                    logger.info(
                        f"  Receipt subtotal: {receipt_subtotal}, Line item subtotal: {line_item_subtotal}"
                    )
                    logger.info(
                        f"  Receipt tax: {receipt_tax}, Line item tax: {line_item_tax}"
                    )

                    # Check for discrepancies
                    discrepancies = []
                    warnings = []
                    critical_errors = []

                    # Add discrepancies from line item analysis if we're using inferred values
                    if line_item_analysis.discrepancies and use_inferred:
                        discrepancies.extend(line_item_analysis.discrepancies)

                    # Check total match - only if both values exist and we're using inferred values
                    # If we're not using inferred values, skip this check
                    if (
                        receipt_total is not None
                        and line_item_total is not None
                        and use_inferred
                    ):
                        difference = abs(receipt_total - line_item_total)
                        allowed_difference = receipt_total * (
                            self.validation_config["discrepancy_percentage"]
                            / 100.0
                        )

                        if difference > allowed_difference:
                            discrepancy_reason = (
                                f"Receipt total ({receipt_total:.2f}) doesn't match "
                                f"line item total ({line_item_total:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            # Always treat total discrepancies as warnings if configured to do so
                            if self.validation_config[
                                "treat_total_discrepancy_as_warning"
                            ]:
                                warnings.append(discrepancy_reason)
                            elif self.validation_config[
                                "treat_nontotal_errors_as_warnings"
                            ]:
                                warnings.append(discrepancy_reason)
                            else:
                                discrepancies.append(discrepancy_reason)

                    # Check subtotal match - only if both values exist and we're using inferred values
                    if (
                        receipt_subtotal is not None
                        and line_item_subtotal is not None
                        and use_inferred
                    ):
                        difference = abs(receipt_subtotal - line_item_subtotal)
                        allowed_difference = receipt_subtotal * (
                            self.validation_config["discrepancy_percentage"]
                            / 100.0
                        )

                        if difference > allowed_difference:
                            discrepancy_reason = (
                                f"Receipt subtotal ({receipt_subtotal:.2f}) doesn't match "
                                f"line item subtotal ({line_item_subtotal:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            if self.validation_config[
                                "treat_nontotal_errors_as_warnings"
                            ]:
                                warnings.append(discrepancy_reason)
                            else:
                                discrepancies.append(discrepancy_reason)

                    # Check tax match - only if both values exist and we're using inferred values
                    if (
                        receipt_tax is not None
                        and line_item_tax is not None
                        and use_inferred
                    ):
                        difference = abs(receipt_tax - line_item_tax)
                        allowed_difference = (
                            receipt_tax
                            * (
                                self.validation_config[
                                    "discrepancy_percentage"
                                ]
                                / 100.0
                            )
                            if receipt_tax > 0
                            else 0.01
                        )

                        if difference > allowed_difference:
                            discrepancy_reason = (
                                f"Receipt tax ({receipt_tax:.2f}) doesn't match "
                                f"line item tax ({line_item_tax:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            if self.validation_config[
                                "treat_nontotal_errors_as_warnings"
                            ]:
                                warnings.append(discrepancy_reason)
                            else:
                                discrepancies.append(discrepancy_reason)

                    # Check for missing total value (the only required financial value)
                    missing_required_values = []

                    # First, try to find a total from existing data
                    found_total = False

                    # Check for explicit total in receipt or line item analysis
                    if receipt_total is not None:
                        found_total = True
                    elif line_item_total is not None and use_inferred:
                        found_total = True

                    # If not using inferred values, don't try to find totals from line items
                    if not found_total and use_inferred:
                        potential_total = None
                        potential_total_description = None

                        # Expanded list of total phrases
                        total_phrases = [
                            "total",
                            "balance due",
                            "amount due",
                            "grand total",
                            "payment due",
                            "due",
                            "pay",
                            "balance",
                            "payment total",
                            "order total",
                            "final amount",
                            "to pay",
                            "please pay",
                        ]

                        # Look for line items with total-like descriptions
                        for item in line_item_analysis.items:
                            if hasattr(item, "description") and any(
                                phrase in item.description.lower()
                                for phrase in total_phrases
                            ):
                                potential_total = (
                                    float(item.price.extended_price)
                                    if hasattr(item.price, "extended_price")
                                    else None
                                )
                                potential_total_description = item.description

                                # If we found a potential total, use it
                                if potential_total is not None:
                                    logger.info(
                                        f"Found potential total from line item '{potential_total_description}': {potential_total}"
                                    )
                                    found_total = True
                                    # Add a synthetic total to line item analysis
                                    line_item_total = potential_total
                                    break

                    # Check for required values based on configuration
                    if (
                        self.validation_config["require_total"]
                        and not found_total
                    ):
                        message = "Missing total amount"
                        if self.validation_config["missing_total_error"]:
                            critical_errors.append(message)
                        else:
                            warnings.append(message)

                    # Check for date
                    if (
                        self.validation_config["require_date"]
                        and not receipt_date
                    ):
                        message = "Missing date"
                        if self.validation_config["missing_date_error"]:
                            critical_errors.append(message)
                        else:
                            warnings.append(message)

                    # Check for business name
                    if (
                        self.validation_config["require_business_name"]
                        and not business_name
                    ):
                        message = "Missing business name"
                        if self.validation_config[
                            "missing_business_name_error"
                        ]:
                            critical_errors.append(message)
                        else:
                            warnings.append(message)

                    # Check for subtotal - only if we're using inferred values or if receipt_subtotal exists
                    if (
                        (not use_inferred and receipt_subtotal is None)
                        or (
                            use_inferred
                            and line_item_subtotal is None
                            and receipt_subtotal is None
                        )
                    ) and self.validation_config["require_subtotal"]:
                        message = "Missing subtotal value"
                        if self.validation_config["missing_subtotal_warning"]:
                            warnings.append(message)
                        else:
                            discrepancies.append(message)
                    elif (not use_inferred and receipt_subtotal is None) or (
                        use_inferred
                        and line_item_subtotal is None
                        and receipt_subtotal is None
                    ):
                        logger.info("Missing subtotal value (not required)")

                    # Check for tax - only if we're using inferred values or if receipt_tax exists
                    if (
                        (not use_inferred and receipt_tax is None)
                        or (
                            use_inferred
                            and line_item_tax is None
                            and receipt_tax is None
                        )
                    ) and self.validation_config["require_tax"]:
                        message = "Missing tax value"
                        if self.validation_config["missing_tax_warning"]:
                            warnings.append(message)
                        else:
                            discrepancies.append(message)
                    elif (not use_inferred and receipt_tax is None) or (
                        use_inferred
                        and line_item_tax is None
                        and receipt_tax is None
                    ):
                        logger.info("Missing tax value (not required)")

                    # Create a ValidationAnalysis object
                    validation_analysis = ValidationAnalysis()

                    # Add critical errors to validation
                    for error in critical_errors:
                        validation_analysis.line_item_validation.add_result(
                            ValidationResult(
                                type=ValidationResultType.ERROR,
                                message="Critical validation error",
                                reasoning=error,
                            )
                        )

                    # Add regular discrepancies to validation
                    for discrepancy in discrepancies:
                        validation_analysis.line_item_validation.add_result(
                            ValidationResult(
                                type=ValidationResultType.ERROR,
                                message="Line item discrepancy found",
                                reasoning=discrepancy,
                            )
                        )

                    # Add warnings to validation
                    for warning in warnings:
                        validation_analysis.line_item_validation.add_result(
                            ValidationResult(
                                type=ValidationResultType.WARNING,
                                message="Validation warning",
                                reasoning=warning,
                            )
                        )

                    # Set overall reasoning
                    if critical_errors:
                        validation_analysis.overall_reasoning = (
                            f"Validation found {len(critical_errors)} critical errors. "
                            + " ".join(critical_errors)
                        )
                        validation_analysis.overall_status = (
                            ValidationStatus.INVALID
                        )
                    elif discrepancies:
                        validation_analysis.overall_reasoning = (
                            f"Validation found {len(discrepancies)} discrepancies. "
                            + " ".join(discrepancies)
                        )
                        validation_analysis.overall_status = (
                            ValidationStatus.NEEDS_REVIEW
                        )
                    elif warnings:
                        validation_analysis.overall_reasoning = (
                            f"Validation found {len(warnings)} warnings. "
                            + " ".join(warnings)
                        )
                        validation_analysis.overall_status = (
                            ValidationStatus.NEEDS_REVIEW
                        )
                    else:
                        validation_analysis.overall_reasoning = (
                            "No issues found during validation."
                        )
                        validation_analysis.overall_status = (
                            ValidationStatus.VALID
                        )

                    logger.info(
                        f"Validation results for receipt {receipt.receipt_id}: "
                        f"Status: {validation_analysis.overall_status}, "
                        f"Critical Errors: {len(critical_errors)}, "
                        f"Discrepancies: {len(discrepancies)}, "
                        f"Warnings: {len(warnings)}"
                    )

                except Exception as e:
                    logger.error(f"Error during validation: {str(e)}")
                    validation_analysis = ValidationAnalysis(
                        overall_reasoning=f"Validation failed due to error: {str(e)}",
                        overall_status=ValidationStatus.INCOMPLETE,
                    )

                    # Add the error to cross_field_consistency
                    validation_analysis.cross_field_consistency.add_result(
                        ValidationResult(
                            type=ValidationResultType.ERROR,
                            message="Validation could not be completed due to an error",
                            reasoning=f"Error during validation: {str(e)}",
                        )
                    )

            # Create LabelingResult object
            result = LabelingResult(
                receipt_id=receipt.receipt_id,
                structure_analysis=structure_analysis,
                field_analysis=field_analysis,
                line_item_analysis=line_item_analysis,
                validation_analysis=validation_analysis,
                places_api_data=places_data,
                execution_times=execution_times,
            )
            logger.debug("LabelingResult created successfully")
            return result

        except Exception as e:
            logger.error(f"Error processing receipt: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Error traceback: {traceback.format_exc()}")
            raise

    def _log_label_application_summary(
        self, applied_labels, updated_labels, skipped_labels
    ):
        """Print a pretty summary of the label application process"""
        divider = "=" * 70

        logger.info(
            f"\n{divider}\n🔄 LINE ITEM LABEL APPLICATION SUMMARY 🔄\n{divider}"
        )

        # Summary of applied labels
        total_applied = sum(len(labels) for labels in applied_labels.values())
        logger.info(f"✅ APPLIED LABELS: {total_applied}")

        if applied_labels:
            for label_type, labels in applied_labels.items():
                logger.info(f"\n  🏷️  {label_type}: {len(labels)} labels")
                # Show first 5 examples at most to keep logs reasonable
                for i, label in enumerate(labels[:5]):
                    logger.info(
                        f"    • '{label['text']}' ({label['position']})"
                    )
                if len(labels) > 5:
                    logger.info(f"    • ... and {len(labels) - 5} more")
        else:
            logger.info("  No labels were applied")

        # Summary of updated labels
        total_updated = sum(len(labels) for labels in updated_labels.values())
        logger.info(f"\n🔄 UPDATED LABELS: {total_updated}")

        if updated_labels:
            for label_type, labels in updated_labels.items():
                logger.info(f"\n  🔀 {label_type}: {len(labels)} labels")
                # Show first 5 examples at most
                for i, label in enumerate(labels[:5]):
                    logger.info(
                        f"    • '{label['text']}' ({label['position']}) - was '{label['previous_label']}'"
                    )
                if len(labels) > 5:
                    logger.info(f"    • ... and {len(labels) - 5} more")
        else:
            logger.info("  No labels were updated")

        # Summary of skipped labels
        total_skipped = sum(len(labels) for labels in skipped_labels.values())
        logger.info(f"\n⏭️  SKIPPED LABELS: {total_skipped}")

        if skipped_labels:
            for label_type, labels in skipped_labels.items():
                logger.info(f"\n  🚫 {label_type}: {len(labels)} labels")
                # Show first 5 examples at most
                for i, label in enumerate(labels[:5]):
                    logger.info(
                        f"    • '{label['text']}' ({label['position']}) - kept '{label['existing_label']}'"
                    )
                if len(labels) > 5:
                    logger.info(f"    • ... and {len(labels) - 5} more")
        else:
            logger.info("  No labels were skipped")

        # Overall statistics
        total_attempted = total_applied + total_updated + total_skipped
        logger.info(f"\n📊 OVERALL STATISTICS:")
        logger.info(f"   Total attempted: {total_attempted}")
        if total_attempted > 0:
            success_rate = (
                (total_applied + total_updated) / total_attempted
            ) * 100
            logger.info(f"   Success rate: {success_rate:.1f}%")
            logger.info(
                f"   Applied: {total_applied} ({(total_applied/total_attempted)*100:.1f}%)"
            )
            logger.info(
                f"   Updated: {total_updated} ({(total_updated/total_attempted)*100:.1f}%)"
            )
            logger.info(
                f"   Skipped: {total_skipped} ({(total_skipped/total_attempted)*100:.1f}%)"
            )

        logger.info(f"{divider}\n")

    def _get_places_data(
        self, receipt_words: List[ReceiptWord]
    ) -> Optional[Dict]:
        """Get business data from Places API."""
        try:
            # Format receipt for Places API
            receipt_dict = {
                "receipt_id": "temp",  # Temporary ID since we don't have receipt ID here
                "words": [
                    {
                        "text": word.text,
                        "extracted_data": None,  # Add extracted data if available
                    }
                    for word in receipt_words
                ],
            }
            # Process as a batch of one receipt (not async)
            results = self.places_processor.process_receipt_batch(
                [receipt_dict]
            )
            if results and len(results) > 0:
                return results[0].get("places_api_match")
            return None
        except Exception as e:
            logger.warning(f"Error getting Places data: {str(e)}")
            return None

    def validate_receipt(
        self,
        receipt: Receipt,
        field_analysis: Optional[LabelAnalysis] = None,
        line_item_analysis: Optional[LineItemAnalysis] = None,
    ) -> Optional[ValidationAnalysis]:
        """Validate a receipt using existing analysis results.

        Args:
            receipt: Receipt object containing metadata
            field_analysis: Existing field analysis results
            line_item_analysis: Existing line item analysis results

        Returns:
            ValidationAnalysis object containing validation results
        """
        if not field_analysis or not line_item_analysis:
            logger.warning(
                "Cannot validate receipt without field and line item analysis"
            )
            return None

        # Create a fake LabelingResult to reuse the existing validation logic
        fake_result = LabelingResult(
            receipt_id=receipt.receipt_id,
            field_analysis=field_analysis,
            line_item_analysis=line_item_analysis,
        )

        # Generate a new result using the label_receipt method with only validation
        full_result = self.label_receipt(
            receipt=receipt,
            receipt_words=[],  # Empty to skip processing
            receipt_lines=[],  # Empty to skip processing
            enable_validation=True,
            enable_places_api=False,
        )

        # Return just the validation analysis
        return full_result.validation_analysis

    def _get_validation_config_from_level(self, level: str) -> Dict:
        """Generate a validation configuration based on the validation level.

        Args:
            level: The validation level ("basic", "strict", or "none")

        Returns:
            Dict containing the validation configuration
        """
        # Default to no validation if level is not recognized
        if level.lower() == "none":
            return {"enable_validation": False}

        # Basic validation - mapped from the gpt_labeler_with_package.py file
        elif level.lower() == "basic":
            return {
                "enable_validation": True,
                "require_total": True,  # Missing total is an error
                "require_subtotal": False,  # Missing subtotal is not an error
                "require_tax": False,  # Missing tax is not an error
                "require_date": True,  # Missing date is an error
                "require_business_name": True,  # Missing business name is an error
                "validation_level": "basic",
                "allow_discrepancies": True,
                "discrepancy_percentage": 10.0,  # Allow up to 10% discrepancy
                "treat_nontotal_errors_as_warnings": True,  # Only total errors are critical
                "treat_total_discrepancy_as_warning": True,  # Total discrepancy is a warning
                "missing_total_error": True,  # Explicitly mark missing total as error
                "missing_subtotal_warning": True,  # Make missing subtotal a warning
                "missing_tax_warning": True,  # Make missing tax a warning
                "missing_date_error": True,  # Make missing date an error
                "missing_business_name_error": True,  # Make missing business name an error
                "use_inferred_values": False,  # Only use explicitly labeled values
            }

        # Strict validation - requires all fields and exact matches
        elif level.lower() == "strict":
            return {
                "enable_validation": True,
                "require_total": True,  # Missing total is an error
                "require_subtotal": True,  # Missing subtotal is an error
                "require_tax": True,  # Missing tax is an error
                "require_date": True,  # Missing date is an error
                "require_business_name": True,  # Missing business name is an error
                "validation_level": "strict",
                "allow_discrepancies": False,  # No discrepancies allowed
                "discrepancy_percentage": 0.0,  # 0% discrepancy allowed
                "treat_nontotal_errors_as_warnings": False,  # All errors are critical
                "treat_total_discrepancy_as_warning": False,  # Total discrepancy is an error
                "missing_total_error": True,  # Missing total is an error
                "missing_subtotal_warning": False,  # Missing subtotal is an error
                "missing_tax_warning": False,  # Missing tax is an error
                "missing_date_error": True,  # Missing date is an error
                "missing_business_name_error": True,  # Missing business name is an error
                "use_inferred_values": False,  # Only use explicitly labeled values
            }

        # Default to basic validation for any other value
        else:
            return self._get_validation_config_from_level("basic")

    def process_receipt_by_id(
        self,
        receipt_id: int,
        image_id: str,
        enable_validation: Optional[bool] = None,
        enable_places_api: bool = True,
        save_results: bool = True,
        force_reprocess: bool = False,
    ) -> LabelingResult:
        """
        Process a receipt by ID, fetching data from DynamoDB and performing analysis.

        Args:
            receipt_id: The receipt ID to process
            image_id: The image ID containing the receipt
            enable_validation: Whether to perform validation (if None, uses instance setting)
            enable_places_api: Whether to use the Places API for business validation
            save_results: Whether to save analysis results back to DynamoDB
            force_reprocess: If True, always reprocess even if valid analysis exists

        Returns:
            LabelingResult containing the analysis results
        """
        if not self.dynamodb_table_name:
            logger.warning(
                "No DynamoDB table name provided, will not save results"
            )
            save_results = False

        client = (
            DynamoClient(self.dynamodb_table_name)
            if self.dynamodb_table_name
            else None
        )

        # Check for existing analysis if not forcing reprocess
        if client and not force_reprocess:
            logger.info(
                f"Checking for existing analysis for receipt {receipt_id}, image {image_id}"
            )

            # Get all analyses in a single query using the new method
            (
                existing_label_analysis,
                existing_structure_analysis,
                existing_line_item_analysis,
                existing_validation,
            ) = get_receipt_analyses(receipt_id, image_id, client)

            # Check if we have all necessary analyses with the current package version
            current_version = get_package_version()

            if (
                existing_label_analysis
                and existing_structure_analysis
                and existing_line_item_analysis
            ):

                # Get the version from metadata (assuming standardized metadata format)
                label_version = existing_label_analysis.metadata.get(
                    "source_information", {}
                ).get("package_version")
                structure_version = existing_structure_analysis.metadata.get(
                    "source_information", {}
                ).get("package_version")
                line_item_version = existing_line_item_analysis.metadata.get(
                    "source_information", {}
                ).get("package_version")

                if (
                    label_version == current_version
                    and structure_version == current_version
                    and line_item_version == current_version
                ):

                    logger.info(
                        f"Using existing analysis for receipt {receipt_id} (version {current_version})"
                    )

                    # Create result using existing analyses
                    result = LabelingResult(
                        receipt_id=receipt_id,
                        field_analysis=existing_label_analysis,
                        structure_analysis=existing_structure_analysis,
                        line_item_analysis=existing_line_item_analysis,
                    )

                    # If validation is enabled, check for existing validation or create new
                    if enable_validation or (
                        enable_validation is None
                        and self.validation_config["enable_validation"]
                    ):
                        # We already have validation from the get_receipt_analyses call
                        validation_version = (
                            existing_validation.metadata.get(
                                "source_information", {}
                            ).get("package_version")
                            if existing_validation
                            else None
                        )

                        if (
                            existing_validation
                            and validation_version == current_version
                        ):
                            result.validation_analysis = existing_validation
                        else:
                            # Only run validation if needed
                            result.validation_analysis = self.validate_receipt(
                                receipt=None,  # Not needed for validation
                                field_analysis=existing_label_analysis,
                                line_item_analysis=existing_line_item_analysis,
                            )

                            if save_results and result.validation_analysis:
                                self._update_metadata_with_version(
                                    result.validation_analysis.metadata
                                )
                                # Save just the validation analysis
                                self._save_validation_analysis(
                                    result.validation_analysis,
                                    get_package_version(),
                                )

                    return result

        # Existing analysis not found or version mismatch, proceed with normal processing
        logger.info(f"Processing receipt {receipt_id} from image {image_id}")

        # Get receipt data from DynamoDB
        if not client:
            raise ValueError(
                "DynamoDB client is required for processing receipts by ID"
            )

        # Get receipt details including lines
        (
            receipt_data,
            receipt_lines_data,
            receipt_words_data,
            receipt_letters,
            receipt_word_labels,
        ) = client.getReceiptDetails(image_id, receipt_id)
        if not receipt_data:
            raise ValueError(
                f"Receipt {receipt_id} not found for image {image_id}"
            )

        # Fetch receipt words...
        receipt_words = [
            ReceiptWord.from_dynamo(word) for word in receipt_words_data
        ]

        # Fetch receipt lines...
        receipt_lines = [
            ReceiptLine.from_dynamo(line) for line in receipt_lines_data
        ]

        # Convert to Receipt object
        receipt = Receipt.from_dynamo(
            receipt_data=receipt_data, words=receipt_words, lines=receipt_lines
        )

        # Process the receipt
        result = self.label_receipt(
            receipt=receipt,
            receipt_words=receipt_words,
            receipt_lines=receipt_lines,
            enable_validation=enable_validation,
            enable_places_api=enable_places_api,
        )

        # Add version information to metadata
        current_version = get_package_version()
        self._update_metadata_with_version(result.field_analysis.metadata)
        self._update_metadata_with_version(result.structure_analysis.metadata)
        self._update_metadata_with_version(result.line_item_analysis.metadata)
        if result.validation_analysis:
            self._update_metadata_with_version(
                result.validation_analysis.metadata
            )

        # Save results if requested
        if save_results:
            # Add direct debugging print statements
            print("==== DEBUG: ABOUT TO SAVE RESULTS ====")
            if hasattr(result, "field_analysis") and result.field_analysis:
                print(
                    f"Field analysis has {len(getattr(result.field_analysis, 'labels', []))} labels"
                )
                if (
                    hasattr(result.field_analysis, "labels")
                    and result.field_analysis.labels
                ):
                    for i, label in enumerate(
                        result.field_analysis.labels[:3]
                    ):
                        print(
                            f"Label {i}: {label.text} - {label.label} (L{label.line_id}W{label.word_id})"
                        )
            print("=====================================")

            self._save_analysis_results(result, receipt_id, image_id)

        return result

    def _save_analysis_results(self, analysis_result, receipt_id, image_id):
        """Save all analysis results to DynamoDB."""
        try:
            # Create a DynamoClient using the table name
            from receipt_dynamo.data.dynamo_client import DynamoClient

            if not self.dynamodb_table_name:
                logger.error(
                    "No DynamoDB table name provided, cannot save results"
                )
                return False

            client = DynamoClient(table_name=self.dynamodb_table_name)

            # Import the save_analysis_transaction function
            from receipt_label.data.analysis_operations import (
                save_analysis_transaction,
                save_label_analysis,
                save_validation_analysis,
            )

            # Create a transaction to save all analyses
            success = save_analysis_transaction(
                label_analysis=getattr(
                    analysis_result, "field_analysis", None
                ),
                structure_analysis=getattr(
                    analysis_result, "structure_analysis", None
                ),
                line_item_analysis=getattr(
                    analysis_result, "line_item_analysis", None
                ),
                client=client,
                receipt_id=receipt_id,
                image_id=image_id,
            )

            # Debug logging for label saving
            if (
                hasattr(analysis_result, "field_analysis")
                and analysis_result.field_analysis
            ):
                logger.info(
                    f"DEBUG: About to save field_analysis with {len(getattr(analysis_result.field_analysis, 'labels', []))} labels"
                )
                # Try to save labels directly from field_analysis for debugging purposes
                save_result = save_label_analysis(
                    analysis_result.field_analysis, client
                )
                logger.info(
                    f"DEBUG: Direct call to save_label_analysis returned: {save_result}"
                )

            # Save validation results separately (not part of transaction due to split storage model)
            if (
                hasattr(analysis_result, "validation_analysis")
                and analysis_result.validation_analysis
            ):
                logger.info("Saving validation analysis...")
                logger.info(
                    f"Saving validation analysis for model version: {getattr(analysis_result.validation_analysis, 'model_version', '0.1.0')}"
                )
                validation_success = save_validation_analysis(
                    validation_analysis=analysis_result.validation_analysis,
                    client=client,
                    receipt_id=receipt_id,
                    image_id=image_id,
                )
                success = success and validation_success

            return success
        except Exception as e:
            logger.error(f"Error saving analysis results: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def _update_metadata_with_version(self, metadata):
        """Update metadata with the current package version."""
        if metadata:
            metadata["source_information"] = metadata.get(
                "source_information", {}
            )
            metadata["source_information"][
                "package_version"
            ] = get_package_version()

    def _save_validation_analysis(self, validation_analysis, version):
        """Save validation analysis to DynamoDB."""
        if validation_analysis:
            validation_analysis.model_version = version
            self._save_analysis_results(validation_analysis, None, None)
