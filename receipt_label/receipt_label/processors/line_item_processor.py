from typing import Dict, List, Optional, Tuple, Union, Literal
from decimal import Decimal, InvalidOperation
import logging
import re
from dataclasses import dataclass, asdict
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItem, LineItemAnalysis, Price, Quantity
from ..models.uncertainty import MultipleAmountsUncertainty, MissingComponentUncertainty, TotalMismatchUncertainty, UncertaintyItem
from .llm_processor import LLMProcessor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

@dataclass
class CurrencyMatch:
    """Represents a matched currency amount in text."""
    amount: Decimal
    reasoning: str
    text: str
    position: Dict[str, int]  # x, y coordinates
    line_id: str

@dataclass
class ProcessingResult:
    """Holds the results of each processing stage."""
    line_items: List[LineItem]
    subtotal: Optional[Decimal]
    tax: Optional[Decimal]
    total: Optional[Decimal]
    reasoning: str = ""
    uncertain_items: List[UncertaintyItem] = None
    
    def __post_init__(self):
        if self.uncertain_items is None:
            self.uncertain_items = []

class FastPatternMatcher:
    """Handles quick initial pattern matching for currency and line items."""
    
    def __init__(self):
        # Common currency patterns
        self.currency_patterns = [
            r'\$?\d+\.\d{2}\b',  # $XX.XX or XX.XX
            r'\$\d{1,3}(?:,\d{3})*\.\d{2}\b',  # $X,XXX.XX
            r'\b\d+\s*@\s*\$?\d+\.\d{2}\b',  # Quantity @ price pattern
        ]
    
    def find_currency_amounts(self, text: str, line_id: str, position: Dict[str, int]) -> List[CurrencyMatch]:
        """Extract currency amounts from text with position information."""
        matches = []
        for pattern in self.currency_patterns:
            for match in re.finditer(pattern, text):
                try:
                    # Clean the matched text
                    amount_str = match.group(0).replace('$', '').replace(',', '')
                    # Handle quantity @ price pattern
                    if '@' in amount_str:
                        qty, price = amount_str.split('@')
                        amount = Decimal(qty.strip()) * Decimal(price.strip())
                    else:
                        amount = Decimal(amount_str)
                    
                    matches.append(CurrencyMatch(
                        amount=amount,
                        reasoning="Exact match",
                        text=match.group(0),
                        position=position,
                        line_id=line_id
                    ))
                except (ValueError, InvalidOperation):
                    logger.debug(f"Failed to parse amount from {match.group(0)}")
                    continue
        
        return matches

    def process(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
    ) -> ProcessingResult:
        """Process receipt using pattern matching for quick initial analysis.
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            
        Returns:
            ProcessingResult containing initial analysis
        """
        currency_matches = []
        line_items = []
        uncertain_items = []
        
        # First pass: Find all currency amounts
        for word in receipt_words:
            # Get bounding box data - fail if not available
            if not hasattr(word, 'bounding_box'):
                raise ValueError(f"Word missing required bounding_box attribute: {word}")
            
            if isinstance(word.bounding_box, dict):
                if 'x' not in word.bounding_box or 'y' not in word.bounding_box:
                    raise ValueError(f"Bounding box missing required x,y coordinates: {word.bounding_box}")
                position = word.bounding_box
            else:
                if not hasattr(word.bounding_box, 'x') or not hasattr(word.bounding_box, 'y'):
                    raise ValueError(f"Bounding box object missing required x,y attributes: {word.bounding_box}")
                position = {
                    "x": word.bounding_box.x,
                    "y": word.bounding_box.y
                }

            # Get line_id - fail if not available
            if hasattr(word, 'line_id'):
                line_id = word.line_id
            elif isinstance(word, dict) and 'line_id' in word:
                line_id = word['line_id']
            else:
                raise ValueError(f"Word missing required line_id: {word}")
            
            # Get text - fail if not available
            if hasattr(word, 'text'):
                text = word.text
            elif isinstance(word, dict) and 'text' in word:
                text = word['text']
            else:
                raise ValueError(f"Word missing required text: {word}")
            
            matches = self.find_currency_amounts(
                text,
                line_id,
                position
            )
            currency_matches.extend(matches)
        
        # Group currency amounts by line
        amounts_by_line = {}
        for match in currency_matches:
            if match.line_id not in amounts_by_line:
                amounts_by_line[match.line_id] = []
            amounts_by_line[match.line_id].append(match)
        
        # Process each line
        subtotal = None
        tax = None
        total = None
        
        for line in receipt_lines:
            # Handle both object and dictionary formats
            line_id = line.line_id if hasattr(line, 'line_id') else line.get('line_id', '')
            line_text = line.text.lower() if hasattr(line, 'text') else line.get('text', '').lower()
            
            amounts = amounts_by_line.get(line_id, [])
            
            # Process potential line items first
            if amounts:
                # Check for quantity pattern (X @ $Y.YY)
                quantity = None
                unit_price = None
                item_total = amounts[-1].amount
                
                if '@' in line_text:
                    # Try to extract quantity and unit price
                    try:
                        qty_str = line_text.split('@')[0].strip().split()[-1]
                        qty = Decimal(qty_str)
                        # The unit price will be the last amount found
                        unit_price = amounts[-1].amount
                        item_total = qty * unit_price
                        quantity = Quantity(amount=qty, unit=None)
                    except (ValueError, InvalidOperation, IndexError):
                        # If parsing fails, fall back to using the last amount
                        pass
                
                # Create line item
                item = LineItem(
                    description=line_text,
                    quantity=quantity,
                    price=Price(
                        unit_price=unit_price,
                        extended_price=item_total
                    ),
                    reasoning="Identified through pattern matching of price and quantity formats",
                    line_ids=[line_id]
                )
                line_items.append(item)
            
            # Then check for total-related lines and set the appropriate values
            if amounts:
                line_type = self._detect_line_type(line_text)
                if line_type == "subtotal":
                    subtotal = amounts[-1].amount
                elif line_type == "tax":
                    tax = amounts[-1].amount
                elif line_type == "total":
                    total = amounts[-1].amount
            
            # Mark lines with multiple amounts as uncertain
            if len(amounts) > 1:
                uncertain_items.append(MultipleAmountsUncertainty(
                    line=line,
                    amounts=[m.amount for m in amounts]
                ))
        
        # Generate reasoning for the analysis
        reasoning = self._generate_reasoning(subtotal, tax, total, line_items)
        
        return ProcessingResult(
            line_items=line_items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            reasoning=reasoning,
            uncertain_items=uncertain_items
        )
    
    def _detect_line_type(self, text: str) -> Optional[str]:
        """Detect the type of line based on text patterns.
        
        Args:
            text: Line text to analyze
            
        Returns:
            String indicating line type or None
        """
        # Common patterns for special lines
        subtotal_patterns = ['sub total', 'subtotal', 'sub-total']
        tax_patterns = ['tax', 'vat', 'gst', 'hst']
        total_patterns = ['total', 'amount due', 'amount paid', 'grand total']
        
        text = text.lower()
        
        if any(pattern in text for pattern in subtotal_patterns):
            return "subtotal"
        if any(pattern in text for pattern in tax_patterns):
            return "tax"
        if any(pattern in text for pattern in total_patterns) and not any(p in text for p in subtotal_patterns + tax_patterns):
            return "total"
            
        return None
    
    def _generate_reasoning(
        self,
        subtotal: Optional[Decimal],
        tax: Optional[Decimal],
        total: Optional[Decimal],
        line_items: List[LineItem]
    ) -> str:
        """Generate reasoning explanation for the pattern matching results.
        
        Args:
            subtotal: Detected subtotal amount
            tax: Detected tax amount
            total: Detected total amount
            line_items: List of detected line items
            
        Returns:
            String explaining the reasoning for the analysis results
        """
        reasoning_parts = []
        
        # Base reasoning
        reasoning_parts.append("Analysis based on pattern matching and structural recognition.")
        
        # Add reasoning based on presence of key components
        if subtotal is not None:
            reasoning_parts.append("Identified clear subtotal amount.")
        else:
            reasoning_parts.append("Could not identify a subtotal amount.")
            
        if tax is not None:
            reasoning_parts.append("Identified tax amount.")
        else:
            reasoning_parts.append("Could not identify a tax amount.")
            
        if total is not None:
            reasoning_parts.append("Identified total amount.")
        else:
            reasoning_parts.append("Could not identify a total amount.")
            
        if line_items:
            reasoning_parts.append(f"Successfully identified {len(line_items)} line items.")
        else:
            reasoning_parts.append("Could not identify any line items.")
            
        # Check mathematical consistency if we have all components
        if all([subtotal, tax, total]):
            if abs(subtotal + tax - total) < Decimal('0.01'):
                reasoning_parts.append("Mathematical consistency verified: subtotal + tax = total.")
            else:
                reasoning_parts.append(f"Mathematical inconsistency: subtotal ({subtotal}) + tax ({tax}) ≠ total ({total}).")
                
            # Additional check: line items sum should match subtotal
            if line_items:
                line_items_sum = sum(item.price.extended_price or Decimal('0') for item in line_items)
                if abs(line_items_sum - subtotal) < Decimal('0.01'):
                    reasoning_parts.append("Line items sum matches the subtotal amount.")
                else:
                    reasoning_parts.append(f"Line items sum ({line_items_sum}) does not match subtotal ({subtotal}).")
                
        return " ".join(reasoning_parts)

class LineItemValidator:
    """Validates line items and identifies uncertain entries."""
    
    def identify_uncertain_items(self, result: ProcessingResult) -> List[UncertaintyItem]:
        """Identify items that need additional processing."""
        uncertain_items = result.uncertain_items.copy()
        
        # Check for missing components
        if result.subtotal is None:
            uncertain_items.append(MissingComponentUncertainty(
                component="subtotal"
            ))
        if result.tax is None:
            uncertain_items.append(MissingComponentUncertainty(
                component="tax"
            ))
        if result.total is None:
            uncertain_items.append(MissingComponentUncertainty(
                component="total"
            ))
        
        # Validate totals
        if result.subtotal and result.line_items:
            calculated_subtotal = sum(item.price.extended_price or Decimal('0') 
                                   for item in result.line_items)
            if abs(calculated_subtotal - result.subtotal) > Decimal('0.01'):
                uncertain_items.append(TotalMismatchUncertainty(
                    calculated=calculated_subtotal,
                    found=result.subtotal
                ))
        
        return uncertain_items

    def validate_full_receipt(self, result: ProcessingResult) -> Dict:
        """Perform final validation of the receipt."""
        validation_results = {
            "status": "valid",
            "warnings": [],
            "errors": []
        }
        
        # Validate individual components
        if not result.line_items:
            validation_results["errors"].append("No line items found")
        
        if result.subtotal is None:
            validation_results["warnings"].append("Missing subtotal")
        
        if result.tax is None:
            validation_results["warnings"].append("Missing tax")
        
        if result.total is None:
            validation_results["errors"].append("Missing total")
        
        # Validate totals if all components are present
        if all([result.subtotal, result.tax, result.total]):
            expected_total = result.subtotal + result.tax
            if abs(expected_total - result.total) > Decimal('0.01'):
                validation_results["errors"].append(
                    f"Total mismatch: {result.subtotal} + {result.tax} != {result.total}"
                )
        
        # Update status based on errors
        if validation_results["errors"]:
            validation_results["status"] = "invalid"
        elif validation_results["warnings"]:
            validation_results["status"] = "valid_with_warnings"
        
        return validation_results

class LineItemProcessor:
    """Processes line items in receipts using a progressive refinement approach."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        self.fast_processor = FastPatternMatcher()
        self.validator = LineItemValidator()
        self.llm_processor = LLMProcessor(gpt_api_key)
        self.gpt_api_key = gpt_api_key
    
    async def process_receipt(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict] = None,
    ) -> LineItemAnalysis:
        """Process a receipt to extract line items.
        
        Args:
            receipt: Receipt object
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            places_api_data: Optional Places API data for context
            
        Returns:
            LineItemAnalysis with extracted line items and totals
        """
        # Stage 1: Fast Pattern Matching
        logger.info("Stage 1: Performing fast pattern matching")
        initial_results = self.fast_processor.process(receipt, receipt_lines, receipt_words)
        
        # Stage 2: Validation and Uncertainty Detection
        logger.info("Stage 2: Assessing receipt data and identifying uncertain items")
        uncertain_items = self.validator.identify_uncertain_items(initial_results)
        
        # Add in any calculated values that weren't detected
        if not initial_results.total and initial_results.subtotal and initial_results.tax:
            initial_results.total = initial_results.subtotal + initial_results.tax
            logger.debug(f"Calculated missing total: {initial_results.total}")
        
        # Stage 3: Apply LLM processing if needed and available
        if uncertain_items and self.llm_processor:
            logger.info("Stage 3: Enhancing results with LLM processing")
            try:
                # Call the LLM processor to handle uncertain items
                llm_updates = await self.llm_processor.process_uncertain_items(
                    uncertain_items=uncertain_items,
                    receipt=receipt,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    initial_state=asdict(initial_results),
                    places_api_data=places_api_data or {}
                )
                
                # Apply any updates from LLM processing
                if llm_updates:
                    logger.info(f"Applying LLM updates: {llm_updates}")
                    if "subtotal" in llm_updates:
                        initial_results.subtotal = llm_updates["subtotal"]
                    if "tax" in llm_updates:
                        initial_results.tax = llm_updates["tax"]
                    if "total" in llm_updates:
                        initial_results.total = llm_updates["total"]
                    if "line_items" in llm_updates:
                        initial_results.line_items = llm_updates["line_items"]
            except Exception as e:
                logger.error(f"Error during LLM processing: {str(e)}")
                # Continue with traditional processing if LLM fails
        
        # Generate reasoning for the analysis
        reasoning = self.fast_processor._generate_reasoning(
            initial_results.subtotal,
            initial_results.tax,
            initial_results.total,
            initial_results.line_items
        )
        
        # Final validation
        validation_results = self.validator.validate_full_receipt(initial_results)
        
        # Create the final analysis
        return LineItemAnalysis(
            items=initial_results.line_items,
            total_found=len(initial_results.line_items),
            subtotal=initial_results.subtotal,
            tax=initial_results.tax,
            total=initial_results.total,
            discrepancies=[f"{type}: {msg}" for type, msg in 
                         ([(k, v) for k, v in validation_results.items() if k != 'status'])],
            reasoning=reasoning,
        )

    async def analyze_line_items(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict] = None
    ) -> LineItemAnalysis:
        """Analyze line items in a receipt.
        
        This method orchestrates the full line item analysis process:
        1. Fast pattern matching for initial detection
        2. Validation and uncertainty detection
        3. LLM-based enhancement for uncertain items (if needed)
        
        Args:
            receipt: Receipt object containing metadata
            receipt_lines: List of ReceiptLine objects
            receipt_words: List of ReceiptWord objects
            places_api_data: Optional dictionary with Places API data
            
        Returns:
            LineItemAnalysis: Analysis results with items, totals, and reasoning
        """
        # Alias for process_receipt to ensure backward compatibility
        return await self.process_receipt(receipt, receipt_lines, receipt_words, places_api_data) 