from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging
from ..models.receipt import Receipt, ReceiptWord, ReceiptSection
from ..processors.gpt import GPTProcessor
from ..processors.structure import StructureProcessor
from ..processors.field import FieldProcessor
from ..data.places_api import BatchPlacesProcessor
from .validator import ReceiptValidator

logger = logging.getLogger(__name__)


@dataclass
class LabelingResult:
    """Result of the labeling process for a receipt."""

    receipt_id: str
    image_id: str
    structure_analysis: Dict
    field_analysis: Dict
    places_api_data: Optional[Dict]
    validation_results: Dict
    overall_confidence: float


class ReceiptLabeler:
    """Main class for orchestrating receipt labeling process."""

    def __init__(
        self,
        places_api_key: str,
        dynamodb_table_name: str,
        gpt_api_key: Optional[str] = None,
    ):
        """Initialize the receipt labeler.

        Args:
            places_api_key: Google Places API key
            dynamodb_table_name: DynamoDB table name for caching
            gpt_api_key: Optional GPT API key for advanced processing
        """
        self.places_processor = BatchPlacesProcessor(
            places_api_key, dynamodb_table_name
        )
        self.gpt_processor = GPTProcessor(gpt_api_key) if gpt_api_key else None
        self.structure_processor = StructureProcessor()
        self.field_processor = FieldProcessor()
        self.validator = ReceiptValidator()

    async def label_receipt(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[Dict],
    ) -> LabelingResult:
        """Label a receipt with business information and fields.

        Args:
            receipt: Receipt data model
            receipt_words: List of words from the receipt
            receipt_lines: List of lines from the receipt

        Returns:
            LabelingResult containing all analysis and validation results
        """
        logger.info(f"Processing receipt {receipt.receipt_id}...")

        # 1. Get Places API data
        places_data = await self._get_places_data(receipt)

        # 2. Analyze structure
        structure_analysis = await self._analyze_structure(
            receipt, receipt_words, receipt_lines, places_data
        )

        # 3. Label fields
        field_analysis = await self._label_fields(
            receipt, receipt_words, receipt_lines, structure_analysis, places_data
        )

        # 4. Validate results
        validation_results = self.validator.validate_receipt_data(
            field_analysis, places_data, receipt_words, self.places_processor
        )

        # Calculate overall confidence
        overall_confidence = self._calculate_confidence(
            structure_analysis, field_analysis
        )

        return LabelingResult(
            receipt_id=receipt.receipt_id,
            image_id=receipt.image_id,
            structure_analysis=structure_analysis,
            field_analysis=field_analysis,
            places_api_data=places_data,
            validation_results=validation_results,
            overall_confidence=overall_confidence,
        )

    async def _get_places_data(self, receipt: Receipt) -> Optional[Dict]:
        """Get business data from Places API."""
        try:
            receipt_dict = {
                "receipt_id": receipt.receipt_id,
                "image_id": receipt.image_id,
                "words": [
                    {"text": word.text, "extracted_data": word.extracted_data}
                    for word in receipt.words
                ],
            }
            enriched_receipt = self.places_processor.process_receipt_batch(
                [receipt_dict]
            )[0]
            return enriched_receipt.get("places_api_match")
        except Exception as e:
            logger.error(f"Error getting Places API data: {str(e)}")
            return None

    async def _analyze_structure(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[Dict],
        places_data: Optional[Dict],
    ) -> Dict:
        """Analyze receipt structure."""
        if self.gpt_processor:
            return await self.gpt_processor.analyze_structure(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                places_api_data=places_data,
            )
        return self.structure_processor.analyze_structure(receipt_words, receipt_lines)

    async def _label_fields(
        self,
        receipt: Receipt,
        receipt_words: List[ReceiptWord],
        receipt_lines: List[Dict],
        structure_analysis: Dict,
        places_data: Optional[Dict],
    ) -> Dict:
        """Label receipt fields."""
        if self.gpt_processor:
            return await self.gpt_processor.label_fields(
                receipt=receipt,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                section_boundaries=structure_analysis,
                places_api_data=places_data,
            )
        return self.field_processor.label_fields(
            receipt_words, receipt_lines, structure_analysis
        )

    def _calculate_confidence(
        self, structure_analysis: Dict, field_analysis: Dict
    ) -> float:
        """Calculate overall confidence score."""
        structure_conf = structure_analysis.get("overall_confidence", 0.0)
        field_conf = field_analysis.get("metadata", {}).get("average_confidence", 0.0)
        return (structure_conf + field_conf) / 2
