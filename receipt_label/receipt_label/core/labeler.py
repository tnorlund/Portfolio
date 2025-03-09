from typing import Dict, List, Optional
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..processors.structure import StructureProcessor
from ..processors.field import FieldProcessor
from ..processors.progressive_processor import ProgressiveReceiptProcessor
from ..processors.gpt import GPTProcessor
from ..data.places_api import BatchPlacesProcessor

logger = logging.getLogger(__name__)

class LabelingResult:
    """Results from receipt labeling process."""
    def __init__(
        self,
        structure_analysis: Dict,
        field_analysis: Dict,
        line_item_analysis: Dict,
        validation_results: Dict,
        places_api_data: Optional[Dict] = None,
        receipt_id: Optional[str] = None,
        image_id: Optional[str] = None,
    ):
        self.structure_analysis = structure_analysis
        self.field_analysis = field_analysis
        self.line_item_analysis = line_item_analysis
        self.validation_results = validation_results
        self.places_api_data = places_api_data
        self.receipt_id = receipt_id
        self.image_id = image_id

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
        self.structure_processor = StructureProcessor()
        self.field_processor = FieldProcessor()
        self.progressive_processor = ProgressiveReceiptProcessor(gpt_api_key=gpt_api_key)
        self.gpt_processor = GPTProcessor(api_key=gpt_api_key)

    async def label_receipt(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
    ) -> LabelingResult:
        """
        Label a receipt with all available processors.

        Args:
            receipt (Receipt): The receipt object containing metadata
            receipt_words (List[ReceiptWord]): List of words from the receipt
            receipt_lines (List[ReceiptLine]): List of lines from the receipt

        Returns:
            LabelingResult: The combined results of all processing
        """
        logger.info(f"Processing receipt {receipt.receipt_id}...")

        try:
            # Get business context from Places API
            places_data = await self._get_places_data(receipt_words)

            # Analyze receipt structure
            structure_analysis = await self._analyze_structure(
                receipt, receipt_words, receipt_lines, places_data
            )

            # Label fields
            field_analysis = await self._label_fields(
                receipt, receipt_words, receipt_lines, structure_analysis, places_data
            )

            # Process line items using progressive processor
            line_item_analysis = await self.progressive_processor.process_receipt(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                places_api_data=places_data
            )

            # Convert line item analysis to dict format for consistency
            line_item_dict = {
                "line_items": [item.__dict__ for item in line_item_analysis.items],
                "total_found": line_item_analysis.total_found,
                "subtotal": str(line_item_analysis.subtotal) if line_item_analysis.subtotal else None,
                "tax": str(line_item_analysis.tax) if line_item_analysis.tax else None,
                "total": str(line_item_analysis.total) if line_item_analysis.total else None,
                "discrepancies": line_item_analysis.discrepancies,
                "confidence": line_item_analysis.confidence
            }

            # The progressive processor includes validation, so we'll use its results
            validation_results = {
                "line_item_validation": line_item_analysis.discrepancies,
                "overall_valid": not any("error" in d.lower() for d in line_item_analysis.discrepancies)
            }

            return LabelingResult(
                structure_analysis=structure_analysis,
                field_analysis=field_analysis,
                line_item_analysis=line_item_dict,
                validation_results=validation_results,
                places_api_data=places_data,
                receipt_id=receipt.receipt_id,
                image_id=receipt.image_id,
            )

        except Exception as e:
            logger.error(f"Error processing receipt: {str(e)}")
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

    async def _analyze_structure(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        places_data: Optional[Dict],
    ) -> Dict:
        """Analyze receipt structure using GPT."""
        try:
            return await self.gpt_processor.analyze_structure(
                receipt=receipt,
                receipt_words=receipt_words,
                receipt_lines=receipt_lines,
                places_api_data=places_data or {},
            )
        except Exception as e:
            logger.error(f"Error in structure analysis: {str(e)}")
            return {
                "discovered_sections": [],
                "overall_confidence": 0.0
            }

    async def _label_fields(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[ReceiptLine],
        structure_analysis: Dict,
        places_data: Optional[Dict],
    ) -> Dict:
        """Label receipt fields using GPT."""
        return await self.gpt_processor.label_fields(
            receipt=receipt,
            receipt_words=receipt_words,
            receipt_lines=receipt_lines,
            section_boundaries=structure_analysis,
            places_api_data=places_data,
        )
