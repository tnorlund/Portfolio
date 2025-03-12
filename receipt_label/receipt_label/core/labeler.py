from typing import Dict, List, Optional, Tuple, Union, Any
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItemAnalysis, LineItem
from ..models.structure import StructureAnalysis
from ..models.label import LabelAnalysis, WordLabel
from ..models.validation import ValidationAnalysis, ValidationResult, ValidationResultType
from ..data.places_api import BatchPlacesProcessor
from ..processors.receipt_analyzer import ReceiptAnalyzer
from ..processors.line_item_processor import LineItemProcessor
import traceback
import asyncio
import time
from datetime import datetime

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
        line_items: List[LineItemAnalysis] = None,
        validation_results: ValidationAnalysis = None,
        places_api_data: Dict = None,
        execution_times: Dict = None,
        errors: Dict = None,
    ):
        """Initialize LabelingResult object.
        
        Args:
            receipt_id: Unique identifier for the receipt
            structure_analysis: Results from structure analysis
            field_analysis: Results from field labeling
            line_items: List of analyzed line items
            validation_results: Results from validation checks
            places_api_data: Data retrieved from Places API
            execution_times: Dictionary of execution times for different steps
            errors: Dictionary of errors encountered during processing
        """
        self.receipt_id = receipt_id
        self.structure_analysis = structure_analysis
        self.field_analysis = field_analysis
        self.line_items = line_items if line_items else []
        self.validation_results = validation_results
        self.places_api_data = places_api_data
        self.execution_times = execution_times if execution_times else {}
        self.errors = errors if errors else {}

    def to_dict(self) -> Dict:
        """Convert LabelingResult to dictionary format.
        
        Returns:
            Dictionary representation of LabelingResult
        """
        line_items_dict = [li.to_dict() if hasattr(li, 'to_dict') else li for li in self.line_items]
        
        return {
            "receipt_id": self.receipt_id,
            "structure_analysis": self.structure_analysis.to_dict() if self.structure_analysis else None,
            "field_analysis": self.field_analysis.to_dict() if self.field_analysis else None,
            "line_items": line_items_dict,
            "validation_results": self.validation_results.to_dict() if self.validation_results else None,
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
    ):
        """Initialize the labeler with optional API keys."""
        self.places_processor = BatchPlacesProcessor(
            api_key=places_api_key,
            dynamo_table_name=dynamodb_table_name,
        )
        self.receipt_analyzer = ReceiptAnalyzer(api_key=gpt_api_key)
        self.line_item_processor = LineItemProcessor(gpt_api_key=gpt_api_key)

    async def label_receipt(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        enable_validation: bool = True,
        enable_places_api: bool = True,
    ) -> LabelingResult:
        """Label a receipt using various processors.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_words: List of ReceiptWord objects
            receipt_lines: List of ReceiptLine objects
            enable_validation: Whether to perform validation checks
            enable_places_api: Whether to use Places API for additional context

        Returns:
            LabelingResult object containing all analysis results
        """
        logger.info(f"Processing receipt {receipt.receipt_id}...")
        
        # Initialize tracking dictionaries
        execution_times = {}
        errors = {}
        
        try:
            # Get Places API data if enabled
            places_data = None
            if enable_places_api:
                start_time = time.time()
                places_data = await self._get_places_data(receipt_words)
                execution_times["places_api"] = time.time() - start_time

            # Analyze receipt structure
            logger.debug("Starting structure analysis...")
            structure_analysis = await self.receipt_analyzer.analyze_structure(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                places_api_data=places_data
            )
            logger.debug("Structure analysis completed successfully")
            logger.debug(f"Structure analysis sections: {len(structure_analysis.sections)} found")

            # Label fields
            logger.debug("Starting field labeling...")
            field_analysis = await self.receipt_analyzer.label_fields(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                section_boundaries=structure_analysis,
                places_api_data=places_data
            )
            logger.debug("Field labeling completed successfully")
            logger.debug(f"Field analysis found {len(field_analysis.labels) if hasattr(field_analysis, 'labels') else 0} labels")
            if hasattr(field_analysis, 'metadata') and field_analysis.metadata:
                logger.debug(f"Field analysis has metadata: {field_analysis.metadata}")

            # Process line items using line item processor
            logger.info("Processing line items")
            line_item_processor = LineItemProcessor()
            start_time = time.time()
            line_item_analysis = await line_item_processor.analyze_line_items(
                receipt, receipt_lines, receipt_words, places_data, structure_analysis
            )
            execution_times["line_item_processing"] = time.time() - start_time
            
            if line_item_analysis:
                logger.info(f"Successfully processed {len(line_item_analysis.items)} line items")
                
                # Apply line item word labels to field analysis if available
                if hasattr(line_item_analysis, 'word_labels') and line_item_analysis.word_labels:
                    logger.info(f"Applying {len(line_item_analysis.word_labels)} line item word labels")
                    
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
                        "MISC": 1
                    }
                    
                    # Create new labels from line item word labels
                    for (line_id, word_id), label_info in line_item_analysis.word_labels.items():
                        # Find the corresponding word
                        for word in receipt_words:
                            if word.line_id == line_id and word.word_id == word_id:
                                # Check if this word already has a label
                                existing_label = next((label for label in field_analysis.labels 
                                                     if label.line_id == line_id and label.word_id == word_id), None)
                                
                                if not existing_label:
                                    # Add new label if no existing label
                                    field_analysis.labels.append(
                                        WordLabel(
                                            text=word.text,
                                            label=label_info["label"],
                                            line_id=line_id,
                                            word_id=word_id,
                                            reasoning=label_info["reasoning"],
                                            bounding_box=word.bounding_box if hasattr(word, 'bounding_box') else None
                                        )
                                    )
                                    # Add to applied labels for logging
                                    if label_info["label"] not in applied_labels:
                                        applied_labels[label_info["label"]] = []
                                    applied_labels[label_info["label"]].append({
                                        "text": word.text,
                                        "position": f"L{line_id}W{word_id}"
                                    })
                                    logger.debug(f"Added new label '{label_info['label']}' to word '{word.text}'")
                                else:
                                    # Decide whether to update the existing label
                                    new_label = label_info["label"]
                                    old_label = existing_label.label
                                    
                                    # Get priorities (default to 0 if not found) - use uppercase for case-insensitive comparison
                                    new_priority = label_priority.get(new_label.upper(), 0)
                                    old_priority = label_priority.get(old_label.upper(), 0)
                                    
                                    # Update if the new label has higher priority OR if they're the same label type (case-insensitive match)
                                    # but the new one is from the line item processor (uppercase)
                                    if new_priority > old_priority or (new_label.upper() == old_label.upper() and new_label.isupper()):
                                        # Keep track of the old label for reference
                                        old_reasoning = existing_label.reasoning
                                        
                                        # Update the existing label
                                        existing_label.label = new_label
                                        existing_label.reasoning = f"{label_info['reasoning']} (Previously labeled as '{old_label}': {old_reasoning})"
                                        
                                        # Track for logging
                                        if new_label not in updated_labels:
                                            updated_labels[new_label] = []
                                        updated_labels[new_label].append({
                                            "text": word.text,
                                            "position": f"L{line_id}W{word_id}",
                                            "previous_label": old_label
                                        })
                                        logger.debug(f"Updated label from '{old_label}' to '{new_label}' for word '{word.text}'")
                                    else:
                                        # If we're not updating, still enhance the reasoning if possible
                                        if "item_index" in label_info:
                                            # Enhance the existing label with line item context
                                            item_idx = label_info["item_index"]
                                            if item_idx is not None and 0 <= item_idx < len(line_item_analysis.items):
                                                item = line_item_analysis.items[item_idx]
                                                existing_label.reasoning += f" (Part of line item: {item.description})"
                                        
                                        # Track skipped label
                                        if label_info["label"] not in skipped_labels:
                                            skipped_labels[label_info["label"]] = []
                                        skipped_labels[label_info["label"]].append({
                                            "text": word.text,
                                            "position": f"L{line_id}W{word_id}",
                                            "existing_label": existing_label.label
                                        })
                                        logger.debug(f"Kept existing label '{existing_label.label}' instead of '{label_info['label']}' for word '{word.text}'")
                                
                                break
                    
                    # Print pretty log summary of applied and skipped labels
                    self._log_label_application_summary(applied_labels, updated_labels, skipped_labels)
                    
                    # Update total labeled words count
                    field_analysis.total_labeled_words = len(field_analysis.labels)
                    
                    # Add line item labeling to metadata
                    if "processing_metrics" not in field_analysis.metadata:
                        field_analysis.metadata["processing_metrics"] = {}
                    field_analysis.metadata["processing_metrics"]["line_item_labels"] = {
                        "count": len(line_item_analysis.word_labels),
                        "applied": sum(len(labels) for labels in applied_labels.values()),
                        "updated": sum(len(labels) for labels in updated_labels.values()),
                        "skipped": sum(len(labels) for labels in skipped_labels.values()),
                        "label_types": {label: sum(1 for info in line_item_analysis.word_labels.values() 
                                               if info.get("label") == label)
                                      for label in set(info.get("label") for info in line_item_analysis.word_labels.values())}
                    }
                    
                    # Add an event to the history
                    field_analysis.add_history_event("line_item_labels_applied", {
                        "count": len(line_item_analysis.word_labels),
                        "applied": sum(len(labels) for labels in applied_labels.values()),
                        "updated": sum(len(labels) for labels in updated_labels.values()),
                        "skipped": sum(len(labels) for labels in skipped_labels.values()),
                        "timestamp": datetime.now().isoformat()
                    })
                else:
                    # Check if we have a valid line_item_analysis object but no word labels
                    if line_item_analysis and hasattr(line_item_analysis, 'items') and line_item_analysis.items:
                        logger.info(f"Line item analysis found {len(line_item_analysis.items)} items but no word labels")
                    else:
                        logger.warning("Line item analysis returned None or has no items")
            
            # Create validation results if validation is enabled
            validation_results = None
            if enable_validation and line_item_analysis:
                logger.info(f"Performing validation for receipt {receipt.receipt_id}")
                try:
                    # Extract validation-related data from line items and field analysis
                    receipt_total = None
                    receipt_subtotal = None
                    receipt_tax = None
                    
                    # Extract financial values from field analysis
                    for label in field_analysis.labels:
                        if hasattr(label, 'label') and hasattr(label, 'text'):
                            text_value = label.text.replace('$', '').replace(',', '')
                            try:
                                # Check for total
                                if label.label == 'TOTAL':
                                    receipt_total = float(text_value)
                                # Check for subtotal
                                elif label.label == 'SUBTOTAL':
                                    receipt_subtotal = float(text_value)
                                # Check for tax
                                elif label.label == 'TAX':
                                    receipt_tax = float(text_value)
                            except (ValueError, TypeError):
                                logger.warning(f"Could not convert {label.label} label text '{label.text}' to float")
                    
                    # Get values from line item analysis
                    line_item_total = float(line_item_analysis.total) if line_item_analysis.total else None
                    line_item_subtotal = float(line_item_analysis.subtotal) if line_item_analysis.subtotal else None
                    line_item_tax = float(line_item_analysis.tax) if line_item_analysis.tax else None
                    
                    # Log the extracted values for debugging
                    logger.info("Financial values for validation:")
                    logger.info(f"  Receipt total: {receipt_total}, Line item total: {line_item_total}")
                    logger.info(f"  Receipt subtotal: {receipt_subtotal}, Line item subtotal: {line_item_subtotal}")
                    logger.info(f"  Receipt tax: {receipt_tax}, Line item tax: {line_item_tax}")
                    
                    # Check for discrepancies
                    discrepancies = []
                    
                    # Add discrepancies from line item analysis
                    if line_item_analysis.discrepancies:
                        discrepancies.extend(line_item_analysis.discrepancies)
                    
                    # Check total match
                    if receipt_total is not None and line_item_total is not None:
                        difference = abs(receipt_total - line_item_total)
                        if difference > 0.01:  # Allow for small rounding differences
                            discrepancy_reason = (
                                f"Receipt total ({receipt_total:.2f}) doesn't match "
                                f"line item total ({line_item_total:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            discrepancies.append(discrepancy_reason)
                    
                    # Check subtotal match (only if both values exist)
                    if receipt_subtotal is not None and line_item_subtotal is not None:
                        difference = abs(receipt_subtotal - line_item_subtotal)
                        if difference > 0.01:  # Allow for small rounding differences
                            discrepancy_reason = (
                                f"Receipt subtotal ({receipt_subtotal:.2f}) doesn't match "
                                f"line item subtotal ({line_item_subtotal:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            discrepancies.append(discrepancy_reason)
                    
                    # Check tax match (only if both values exist)
                    if receipt_tax is not None and line_item_tax is not None:
                        difference = abs(receipt_tax - line_item_tax)
                        if difference > 0.01:  # Allow for small rounding differences
                            discrepancy_reason = (
                                f"Receipt tax ({receipt_tax:.2f}) doesn't match "
                                f"line item tax ({line_item_tax:.2f}), "
                                f"difference: {difference:.2f}"
                            )
                            discrepancies.append(discrepancy_reason)
                    
                    # Check for missing total value (the only required financial value)
                    missing_required_values = []
                    
                    # First, try to find a total from existing data
                    found_total = False
                    
                    # Check for explicit total in receipt or line item analysis
                    if receipt_total is not None or line_item_total is not None:
                        found_total = True
                    
                    # If no explicit total, look for line items that might be the total
                    if not found_total:
                        potential_total = None
                        potential_total_description = None
                        
                        # Expanded list of total phrases
                        total_phrases = [
                            "total", "balance due", "amount due", "grand total", 
                            "payment due", "due", "pay", "balance", "payment total", 
                            "order total", "final amount", "to pay", "please pay"
                        ]
                        
                        # Look for line items with total-like descriptions
                        for item in line_item_analysis.items:
                            if (hasattr(item, 'description') and 
                                any(phrase in item.description.lower() for phrase in total_phrases)):
                                potential_total = float(item.price.extended_price) if hasattr(item.price, 'extended_price') else None
                                potential_total_description = item.description
                                
                                # If we found a potential total, use it
                                if potential_total is not None:
                                    logger.info(f"Found potential total from line item '{potential_total_description}': {potential_total}")
                                    found_total = True
                                    # Add a synthetic total to line item analysis
                                    line_item_total = potential_total
                                    break
                    
                    # Only add missing total to required values if we couldn't find one
                    if not found_total:
                        missing_required_values.append("total")
                    
                    # Log missing subtotal and tax as warnings rather than errors
                    if line_item_subtotal is None and receipt_subtotal is None:
                        logger.warning("Missing subtotal value (not a validation error)")
                    
                    if line_item_tax is None and receipt_tax is None:
                        logger.warning("Missing tax value (not a validation error)")
                    
                    # Only add missing required values to discrepancies (total is the only required value)
                    if missing_required_values:
                        missing_values_str = ", ".join(missing_required_values)
                        discrepancies.append(f"Missing required financial values: {missing_values_str}")
                    
                    # Create a ValidationAnalysis object
                    validation_results = ValidationAnalysis(
                        overall_reasoning=(
                            f"Validation found {len(discrepancies)} discrepancies. " +
                            " ".join(discrepancies) if discrepancies else 
                            "No discrepancies found during validation."
                        )
                    )
                    
                    # Add discrepancies to the line_item_validation field
                    if discrepancies:
                        for discrepancy in discrepancies:
                            validation_results.line_item_validation.add_result(
                                ValidationResult(
                                    type=ValidationResultType.ERROR,
                                    message=f"Line item discrepancy found",
                                    reasoning=discrepancy
                                )
                            )
                    
                    logger.info(f"Validation results for receipt {receipt.receipt_id}: "
                                f"{len(discrepancies)} discrepancies found")
                    
                except Exception as e:
                    logger.error(f"Error during validation: {str(e)}")
                    validation_results = ValidationAnalysis(
                        overall_reasoning=f"Validation failed due to error: {str(e)}"
                    )
                    
                    # Add the error to cross_field_consistency
                    validation_results.cross_field_consistency.add_result(
                        ValidationResult(
                            type=ValidationResultType.ERROR,
                            message="Validation could not be completed due to an error",
                            reasoning=f"Error during validation: {str(e)}"
                        )
                    )
            
            # Create LabelingResult object
            result = LabelingResult(
                receipt_id=receipt.receipt_id,
                structure_analysis=structure_analysis,
                field_analysis=field_analysis,
                line_items=[line_item_analysis] if line_item_analysis else [],
                validation_results=validation_results,
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

    def _log_label_application_summary(self, applied_labels, updated_labels, skipped_labels):
        """Print a pretty summary of the label application process"""
        divider = "=" * 70
        
        logger.info(f"\n{divider}\n🔄 LINE ITEM LABEL APPLICATION SUMMARY 🔄\n{divider}")
        
        # Summary of applied labels
        total_applied = sum(len(labels) for labels in applied_labels.values())
        logger.info(f"✅ APPLIED LABELS: {total_applied}")
        
        if applied_labels:
            for label_type, labels in applied_labels.items():
                logger.info(f"\n  🏷️  {label_type}: {len(labels)} labels")
                # Show first 5 examples at most to keep logs reasonable
                for i, label in enumerate(labels[:5]):
                    logger.info(f"    • '{label['text']}' ({label['position']})")
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
                    logger.info(f"    • '{label['text']}' ({label['position']}) - was '{label['previous_label']}'")
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
                    logger.info(f"    • '{label['text']}' ({label['position']}) - kept '{label['existing_label']}'")
                if len(labels) > 5:
                    logger.info(f"    • ... and {len(labels) - 5} more")
        else:
            logger.info("  No labels were skipped")
        
        # Overall statistics
        total_attempted = total_applied + total_updated + total_skipped
        logger.info(f"\n📊 OVERALL STATISTICS:")
        logger.info(f"   Total attempted: {total_attempted}")
        if total_attempted > 0:
            success_rate = ((total_applied + total_updated) / total_attempted) * 100
            logger.info(f"   Success rate: {success_rate:.1f}%")
            logger.info(f"   Applied: {total_applied} ({(total_applied/total_attempted)*100:.1f}%)")
            logger.info(f"   Updated: {total_updated} ({(total_updated/total_attempted)*100:.1f}%)")
            logger.info(f"   Skipped: {total_skipped} ({(total_skipped/total_attempted)*100:.1f}%)")
        
        logger.info(f"{divider}\n")

    async def _get_places_data(self, receipt_words: List[ReceiptWord]) -> Optional[Dict]:
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
            results = self.places_processor.process_receipt_batch([receipt_dict])
            if results and len(results) > 0:
                return results[0].get("places_api_match")
            return None
        except Exception as e:
            logger.warning(f"Error getting Places data: {str(e)}")
            return None
