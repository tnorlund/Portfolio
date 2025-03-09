from typing import Dict, List, Optional, Tuple, Union
import logging
import traceback
from ..models.receipt import Receipt, ReceiptWord, ReceiptSection, ReceiptLine
from ..data.places_api import BatchPlacesProcessor
from ..data.gpt import gpt_request_structure_analysis, gpt_request_field_labeling, gpt_request_line_item_analysis

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ReceiptAnalyzer:
    """Analyzes receipts using GPT for structure, fields, and line items."""

    def __init__(self, api_key: str):
        """Initialize the receipt analyzer.

        Args:
            api_key: GPT API key for analysis
        """
        self.api_key = api_key

    async def analyze_structure(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict],
    ) -> Dict:
        """Analyze receipt structure and layout.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of ReceiptLine objects
            receipt_words: List of ReceiptWord objects
            places_api_data: Optional dictionary containing Places API data

        Returns:
            Dict containing structure analysis results

        Raises:
            TypeError: If any of the input types are incorrect or if list elements are invalid
            ValueError: If any of the required inputs are None or empty or if required attributes are missing
        """
        try:
            # Type checking for main parameters
            if not isinstance(receipt, Receipt):
                raise TypeError(f"Expected receipt to be Receipt object, got {type(receipt)}")
            
            if not isinstance(receipt_lines, list):
                raise TypeError(f"Expected receipt_lines to be list, got {type(receipt_lines)}")
            
            if not isinstance(receipt_words, list):
                raise TypeError(f"Expected receipt_words to be list, got {type(receipt_words)}")
            
            if places_api_data is not None and not isinstance(places_api_data, dict):
                raise TypeError(f"Expected places_api_data to be dict or None, got {type(places_api_data)}")

            # Value checking for lists
            if not receipt_lines:
                raise ValueError("receipt_lines cannot be empty")
            
            if not receipt_words:
                raise ValueError("receipt_words cannot be empty")

            # Detailed type checking for receipt_lines
            required_line_attrs = {'text', 'line_id', 'bounding_box'}
            for i, line in enumerate(receipt_lines):
                if not isinstance(line, ReceiptLine):
                    raise TypeError(f"Item {i} in receipt_lines is {type(line)}, expected ReceiptLine")
                
                # Check for required attributes
                missing_attrs = required_line_attrs - set(dir(line))
                if missing_attrs:
                    raise ValueError(f"ReceiptLine at index {i} is missing required attributes: {missing_attrs}")
                
                # Type check the attributes
                if not isinstance(line.text, str):
                    raise TypeError(f"ReceiptLine.text at index {i} must be str, got {type(line.text)}")
                if not isinstance(line.line_id, int):
                    raise TypeError(f"ReceiptLine.line_id at index {i} must be int, got {type(line.line_id)}")
                if not isinstance(line.bounding_box, dict):
                    raise TypeError(f"ReceiptLine.bounding_box at index {i} must be dict, got {type(line.bounding_box)}")

            # Detailed type checking for receipt_words
            required_word_attrs = {'text', 'line_id', 'word_id', 'bounding_box'}
            for i, word in enumerate(receipt_words):
                if not isinstance(word, ReceiptWord):
                    raise TypeError(f"Item {i} in receipt_words is {type(word)}, expected ReceiptWord")
                
                # Check for required attributes
                missing_attrs = required_word_attrs - set(dir(word))
                if missing_attrs:
                    raise ValueError(f"ReceiptWord at index {i} is missing required attributes: {missing_attrs}")
                
                # Type check the attributes
                if not isinstance(word.text, str):
                    raise TypeError(f"ReceiptWord.text at index {i} must be str, got {type(word.text)}")
                if not isinstance(word.line_id, int):
                    raise TypeError(f"ReceiptWord.line_id at index {i} must be int, got {type(word.line_id)}")
                if not isinstance(word.word_id, int):
                    raise TypeError(f"ReceiptWord.word_id at index {i} must be int, got {type(word.word_id)}")
                if not isinstance(word.bounding_box, dict):
                    raise TypeError(f"ReceiptWord.bounding_box at index {i} must be dict, got {type(word.bounding_box)}")

            # Convert receipt_words to the expected format
            receipt_words_list = []
            for word in receipt_words:
                word_dict = {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "bounding_box": word.bounding_box,
                }
                receipt_words_list.append(word_dict)

            # Convert receipt_lines to the expected format
            receipt_lines_list = []
            for line in receipt_lines:
                line_dict = {
                    "text": line.text,
                    "line_id": line.line_id,
                    "bounding_box": line.bounding_box,
                }
                receipt_lines_list.append(line_dict)

            # Analyze structure
            structure_analysis, query, raw_response = await gpt_request_structure_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug(f"Raw analysis response: {raw_response}")
            return structure_analysis

        except (TypeError, ValueError) as e:
            logger.error(f"Input validation error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error in structure analysis: {str(e)}")
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
        """Label and classify receipt fields."""
        try:
            # Convert receipt_words to the expected format
            receipt_words_list = []
            for word in receipt_words:  # This should only process ReceiptWord objects
                word_dict = {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "bounding_box": word.bounding_box,
                }
                receipt_words_list.append(word_dict)

            # Convert receipt_lines to the expected format
            receipt_lines_list = []
            for line in receipt_lines:  # This should only process ReceiptLine objects
                line_dict = {
                    "text": line.text,
                    "line_id": line.line_id,
                    "bounding_box": line.bounding_box,
                }
                receipt_lines_list.append(line_dict)

            # Label fields
            field_analysis, query, raw_response = await gpt_request_field_labeling(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                section_boundaries=section_boundaries,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug("Field labeling completed")
            logger.debug(f"Field analysis result type: {type(field_analysis)}")
            logger.debug(f"Number of labels generated: {len(field_analysis.get('labels', []))}")

            return field_analysis

        except Exception as e:
            logger.error(f"Error in field labeling: {str(e)}")
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
        """Analyze line items in the receipt."""
        try:
            # Convert receipt_words to the expected format
            receipt_words_list = []
            for word in receipt_words:  # This should only process ReceiptWord objects
                word_dict = {
                    "text": word.text,
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "bounding_box": word.bounding_box,
                }
                receipt_words_list.append(word_dict)

            # Convert receipt_lines to the expected format
            receipt_lines_list = []
            for line in receipt_lines:  # This should only process ReceiptLine objects
                line_dict = {
                    "text": line.text,
                    "line_id": line.line_id,
                    "bounding_box": line.bounding_box,
                }
                receipt_lines_list.append(line_dict)

            # Analyze line items
            line_item_analysis, _, _ = gpt_request_line_item_analysis(
                receipt=receipt,
                receipt_lines=receipt_lines_list,
                receipt_words=receipt_words_list,
                traditional_analysis=traditional_analysis,
                places_api_data=places_api_data,
                gpt_api_key=self.api_key,
            )

            logger.debug(f"Raw line item analysis: {line_item_analysis}")
            return line_item_analysis

        except Exception as e:
            logger.error(f"Error in line item analysis: {str(e)}")
            raise

    def _prepare_prompt(
        self, task: str, receipt_data: Dict, context: Optional[Dict] = None
    ) -> str:
        """Prepare a prompt for analysis.

        Args:
            task: The task to perform (e.g., "structure_analysis", "field_labeling")
            receipt_data: Receipt data to analyze
            context: Additional context for the task

        Returns:
            Formatted prompt string
        """
        # TODO: Implement prompt preparation
        raise NotImplementedError("Prompt preparation not yet implemented")

    async def _call_gpt(
        self, prompt: str, max_tokens: int = 1000, temperature: float = 0.7
    ) -> str:
        """Call GPT with a prompt.

        Args:
            prompt: The prompt to send
            max_tokens: Maximum number of tokens to generate
            temperature: Temperature for response generation

        Returns:
            GPT's response as a string
        """
        # TODO: Implement GPT API call
        raise NotImplementedError("GPT API call not yet implemented")

    def _parse_gpt_response(self, response: str, task: str) -> Dict:
        """Parse analysis response into a structured format.

        Args:
            response: Response string to parse
            task: The task that was performed

        Returns:
            Parsed response as a dictionary
        """
        # TODO: Implement response parsing
        raise NotImplementedError("Response parsing not yet implemented") 