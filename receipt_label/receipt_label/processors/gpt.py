from typing import Dict, List, Optional, Tuple, Union
import logging
import traceback
from ..models.receipt import Receipt, ReceiptWord, ReceiptSection, ReceiptLine
from ..data.places_api import BatchPlacesProcessor
from ..data.gpt import gpt_request_structure_analysis, gpt_request_field_labeling, gpt_request_line_item_analysis

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Set default level to INFO


class GPTProcessor:
    """Handles GPT-specific processing for receipt analysis."""

    def __init__(self, api_key: str):
        """Initialize the GPT processor.

        Args:
            api_key: GPT API key
        """
        self.api_key = api_key

    async def analyze_structure(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict],
    ) -> Dict:
        """Analyze receipt structure using GPT."""
        try:
            # Convert receipt_words to the format expected by gpt_request_structure_analysis
            receipt_words_list = []
            for word in receipt_words:
                if isinstance(word, dict):
                    word_dict = word
                else:
                    word_dict = {
                        "text": word.text,
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "bounding_box": word.bounding_box,
                    }
                receipt_words_list.append(word_dict)

            # Convert receipt_lines to the format expected by gpt_request_structure_analysis
            receipt_lines_list = []
            for line in receipt_lines:
                if isinstance(line, dict):
                    line_dict = line
                else:
                    line_dict = {
                        "text": line.text,
                        "line_id": line.line_id,
                        "bounding_box": line.bounding_box,
                    }
                receipt_lines_list.append(line_dict)

            # Call the GPT structure analysis implementation
            structure_analysis, query, raw_response = await gpt_request_structure_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug(f"Raw GPT response: {raw_response}")  # Changed to debug
            return structure_analysis

        except Exception as e:
            logger.error(f"Error in GPT structure analysis: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise

    async def label_fields(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        section_boundaries: Dict,
        places_api_data: Optional[Dict],
    ) -> Dict:
        """Label receipt fields using GPT."""
        try:
            # Convert receipt_words to the format expected by gpt_request_field_labeling
            receipt_words_list = []
            for word in receipt_words:
                if isinstance(word, dict):
                    word_dict = word
                else:
                    word_dict = {
                        "text": word.text,
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "bounding_box": word.bounding_box,
                    }
                receipt_words_list.append(word_dict)

            # Convert receipt_lines to the format expected by gpt_request_field_labeling
            receipt_lines_list = []
            for line in receipt_lines:
                if isinstance(line, dict):
                    line_dict = line
                else:
                    line_dict = {
                        "text": line.text,
                        "line_id": line.line_id,
                        "bounding_box": line.bounding_box,
                    }
                receipt_lines_list.append(line_dict)

            # Call the GPT field labeling implementation
            field_analysis, query, raw_response = await gpt_request_field_labeling(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                section_boundaries=section_boundaries,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug("Field labeling completed")  # Changed to debug
            logger.debug(f"Field analysis result type: {type(field_analysis)}")  # Changed to debug
            logger.debug(f"Number of labels generated: {len(field_analysis.get('labels', []))}")  # Changed to debug

            return field_analysis

        except Exception as e:
            logger.error(f"Error in GPT field labeling: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise

    async def analyze_line_items(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        traditional_analysis: Dict,
        places_api_data: Optional[Dict],
    ) -> Dict:
        """Analyze line items using GPT.

        Args:
            receipt: Receipt data model
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            traditional_analysis: Results from traditional line item processing
            places_api_data: Places API data

        Returns:
            Dict containing line item analysis results
        """
        try:
            # Convert receipt_words to the format expected by gpt_request_line_item_analysis
            receipt_words_list = [
                {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "bounding_box": word.bounding_box,
                }
                for word in receipt_words
            ]

            # Convert receipt_lines to the format expected by gpt_request_line_item_analysis
            receipt_lines_list = [
                {
                    "text": line.text,
                    "line_id": line.line_id,
                    "bounding_box": line.bounding_box,
                }
                for line in receipt_lines
            ]

            # Call the GPT line item analysis implementation
            line_item_analysis, _, _ = gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                traditional_analysis=traditional_analysis,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug(f"Raw GPT response: {line_item_analysis}")  # Changed to debug
            return line_item_analysis

        except Exception as e:
            logger.error(f"Error in GPT line item analysis: {str(e)}")
            raise

    def _prepare_prompt(
        self, task: str, receipt_data: Dict, context: Optional[Dict] = None
    ) -> str:
        """Prepare a prompt for GPT processing.

        Args:
            task: The task to perform (e.g., "structure_analysis", "field_labeling")
            receipt_data: Receipt data to analyze
            context: Additional context for the task

        Returns:
            Formatted prompt string
        """
        # TODO: Implement prompt preparation
        # This should format the receipt data and context into a prompt
        # that GPT can understand and process
        raise NotImplementedError("Prompt preparation not yet implemented")

    async def _call_gpt(
        self, prompt: str, max_tokens: int = 1000, temperature: float = 0.7
    ) -> str:
        """Call the GPT API with a prompt.

        Args:
            prompt: The prompt to send to GPT
            max_tokens: Maximum number of tokens to generate
            temperature: Temperature for response generation

        Returns:
            GPT's response as a string
        """
        # TODO: Implement GPT API call
        # This should make the actual API call to GPT and handle any errors
        raise NotImplementedError("GPT API call not yet implemented")

    def _parse_gpt_response(self, response: str, task: str) -> Dict:
        """Parse GPT's response into a structured format.

        Args:
            response: GPT's response string
            task: The task that was performed

        Returns:
            Parsed response as a dictionary
        """
        # TODO: Implement response parsing
        # This should parse GPT's response into the appropriate format
        # based on the task that was performed
        raise NotImplementedError("Response parsing not yet implemented")
