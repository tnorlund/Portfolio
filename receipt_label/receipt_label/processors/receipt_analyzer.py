from typing import Dict, List, Optional, Tuple, Union
import logging
import traceback
import re
from ..models.receipt import Receipt, ReceiptWord, ReceiptSection, ReceiptLine
from ..data.places_api import BatchPlacesProcessor
from ..data.gpt import gpt_request_structure_analysis, gpt_request_field_labeling, gpt_request_line_item_analysis
from ..models.structure import StructureAnalysis
from ..models.label import LabelAnalysis

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
    ) -> StructureAnalysis:
        """Analyze receipt structure and layout.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of ReceiptLine objects
            receipt_words: List of ReceiptWord objects
            places_api_data: Optional dictionary containing Places API data

        Returns:
            StructureAnalysis containing structure analysis results with detailed reasoning instead of confidence scores.
            The response includes explanations for why certain sections were identified,
            providing more detailed insights than numerical confidence scores.

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

            # Analyze structure - pass the original objects directly
            logger.info("Calling gpt_request_structure_analysis...")
            result = None
            try:
                result = await gpt_request_structure_analysis(
                    receipt=receipt,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    places_api_data=places_api_data,
                    gpt_api_key=self.api_key,
                )
                logger.info(f"gpt_request_structure_analysis returned {len(result)} values: {type(result)}")
                
                if isinstance(result, tuple):
                    logger.info(f"Result tuple length: {len(result)}")
                    for i, item in enumerate(result):
                        logger.info(f"  Result[{i}] type: {type(item)}")
                
                structure_analysis, query, raw_response = result
                logger.info("Successfully unpacked structure analysis result")
            except ValueError as e:
                logger.error(f"Error unpacking structure analysis result: {str(e)}")
                if result is not None:
                    logger.error(f"Result type: {type(result)}, content: {result}")
                else:
                    logger.error("Result is None")
                raise
            except Exception as e:
                logger.error(f"Other error in structure analysis: {str(e)}")
                raise

            logger.debug(f"Raw analysis response: {raw_response}")
            # Log reasoning fields if they exist
            if isinstance(structure_analysis, dict):
                reasoning_fields = [k for k in structure_analysis if 'reasoning' in k.lower()]
                logger.info(f"Structure analysis reasoning fields: {reasoning_fields}")
            # Convert the dictionary to a StructureAnalysis object
            structure_analysis_obj = StructureAnalysis.from_gpt_response(structure_analysis)
            return structure_analysis_obj

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
        section_boundaries: Union[Dict, StructureAnalysis],
        places_api_data: Optional[Dict],
    ) -> LabelAnalysis:
        """Label and classify receipt fields.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of ReceiptLine objects
            receipt_words: List of ReceiptWord objects
            section_boundaries: Either a dictionary containing section boundaries or a StructureAnalysis object
                from structure analysis
            places_api_data: Optional dictionary containing Places API data

        Returns:
            LabelAnalysis containing field labels with detailed reasoning instead of confidence scores.
            Instead of confidence scores, each label includes detailed reasoning about why
            it was applied, providing more interpretable and transparent results.

        Raises:
            TypeError: If any of the input types are incorrect or if list elements are invalid
            ValueError: If any of the required inputs are None or empty or if required attributes are missing
        """
        try:
            # Label fields - pass the original objects directly
            logger.info("Calling gpt_request_field_labeling...")
            try:
                result = await gpt_request_field_labeling(
                    receipt=receipt,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    section_boundaries=section_boundaries,
                    places_api_data=places_api_data,
                    gpt_api_key=self.api_key,
                )
                logger.info(f"gpt_request_field_labeling returned {len(result)} values: {type(result)}")
                
                if isinstance(result, tuple):
                    logger.info(f"Result tuple length: {len(result)}")
                    for i, item in enumerate(result):
                        logger.info(f"  Result[{i}] type: {type(item)}")
                
                field_analysis, query, raw_response = result
                logger.info("Successfully unpacked field labeling result")
            except ValueError as e:
                logger.error(f"Error unpacking field labeling result: {str(e)}")
                logger.error(f"Result type: {type(result)}, content: {result}")
                raise
            except Exception as e:
                logger.error(f"Other error in field labeling: {str(e)}")
                raise

            logger.debug("Field labeling completed")
            logger.debug(f"Field analysis result type: {type(field_analysis)}")
            logger.debug(f"Number of labels generated: {len(field_analysis.get('labels', []))}")
            
            # Log reasoning fields if they exist
            if isinstance(field_analysis, dict):
                reasoning_fields = []
                for label in field_analysis.get('labels', []):
                    if 'reasoning' in label:
                        reasoning_fields.append(label['label'])
                logger.info(f"Field labels with reasoning: {reasoning_fields}")

                logger.debug(f"Field analysis metadata keys: {list(field_analysis['metadata'].keys())}")
            
            # Convert the dictionary to a LabelAnalysis object
            field_analysis_obj = LabelAnalysis.from_gpt_response(field_analysis)
            
            # Log the number of labels
            logger.debug(f"Number of labels generated: {len(field_analysis_obj.labels)}")
            
            # Log a sample of labels for debugging
            if field_analysis_obj.labels:
                sample_size = min(3, len(field_analysis_obj.labels))
                logger.debug(f"Sample of {sample_size} labels:")
                for label in field_analysis_obj.labels[:sample_size]:
                    logger.debug(f"  {label.text} -> {label.label} (reasoning: {label.reasoning})")
            
            return field_analysis_obj

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
        """Analyze line items in the receipt.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of ReceiptLine objects
            receipt_words: List of ReceiptWord objects
            traditional_analysis: Results from traditional processing methods
            places_api_data: Optional dictionary containing Places API data
            
        Returns:
            Dict containing line item analysis results with reasoning explanations.
            Each identified line item includes explanatory text about why it was
            classified as a line item and details about pricing, quantities, etc.,
            rather than simple confidence scores.
        """
        try:
            # Analyze line items - pass the original objects directly
            logger.info("Calling gpt_request_line_item_analysis...")
            try:
                result = await gpt_request_line_item_analysis(
                    receipt=receipt,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    traditional_analysis=traditional_analysis,
                    places_api_data=places_api_data,
                    gpt_api_key=self.api_key,
                )
                logger.info(f"gpt_request_line_item_analysis returned result of type: {type(result)}")
                
                if isinstance(result, tuple):
                    logger.info(f"Result tuple length: {len(result)}")
                    for i, item in enumerate(result):
                        logger.info(f"  Result[{i}] type: {type(item)}")
                
                line_item_analysis, query, raw_response = result
                logger.info("Successfully unpacked line item analysis result")
            except ValueError as e:
                logger.error(f"Error unpacking line item analysis result: {str(e)}")
                logger.error(f"Result type: {type(result)}, content: {result}")
                raise
            except Exception as e:
                logger.error(f"Other error in line item analysis: {str(e)}")
                raise

            logger.debug(f"Raw line item analysis: {line_item_analysis}")
            
            # Log reasoning fields if they exist
            if isinstance(line_item_analysis, dict):
                reasoning_fields = [k for k in line_item_analysis if 'reasoning' in k.lower()]
                logger.info(f"Line item analysis reasoning fields: {reasoning_fields}")
                
                if 'items' in line_item_analysis:
                    for i, item in enumerate(line_item_analysis['items']):
                        if 'reasoning' in item:
                            logger.info(f"Line item {i} has reasoning: {item['reasoning'][:50]}...")

            return line_item_analysis

        except Exception as e:
            logger.error(f"Error in line item analysis: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
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