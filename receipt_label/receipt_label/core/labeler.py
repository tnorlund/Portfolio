from typing import Dict, List, Optional, Tuple, Union, Any
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItemAnalysis, LineItem
from ..models.structure import StructureAnalysis
from ..models.label import LabelAnalysis
from ..models.validation import ValidationAnalysis, ValidationResult, ValidationResultType
from ..data.places_api import BatchPlacesProcessor
from ..processors.receipt_analyzer import ReceiptAnalyzer
from ..processors.line_item_processor import LineItemProcessor
import traceback
import asyncio
import time

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
                receipt, receipt_lines, receipt_words, places_data
            )
            execution_times["line_item_processing"] = time.time() - start_time
            
            if line_item_analysis:
                logger.info(f"Successfully processed {len(line_item_analysis.items)} line items")
                logger.debug(f"Line item analysis attributes: {dir(line_item_analysis)}")
            else:
                logger.warning("Line item analysis returned None")
                line_item_analysis = LineItemAnalysis(
                    items=[],
                    total_found=0,
                    subtotal=None,
                    tax=None,
                    total=None,
                    discrepancies=["Line item analysis failed"],
                    reasoning="Line item analysis could not be completed"
                )
            
            # Create validation results if validation is enabled
            validation_results = None
            if enable_validation and line_item_analysis:
                logger.info(f"Performing validation for receipt {receipt.receipt_id}")
                try:
                    # Extract validation-related data from line items and field analysis
                    receipt_total = None
                    for label in field_analysis.labels:
                        if hasattr(label, 'label') and label.label == 'total' and hasattr(label, 'text'):
                            try:
                                receipt_total = float(label.text.replace('$', '').replace(',', ''))
                                break
                            except (ValueError, TypeError):
                                logger.warning(f"Could not convert total label text '{label.text}' to float")
                    
                    line_item_total = float(line_item_analysis.total) if line_item_analysis.total else None
                    
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
                line_items=line_item_analysis.items if line_item_analysis else [],
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
