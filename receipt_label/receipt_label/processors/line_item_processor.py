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
    confidence: float
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
    confidence: float
    uncertain_items: List[UncertaintyItem]

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
                        confidence=0.9,  # High confidence for exact matches
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
                    confidence=0.7,  # Medium confidence for initial pass
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
        
        # Calculate confidence based on components and mathematical consistency
        confidence = self._calculate_confidence(subtotal, tax, total, line_items)
        
        return ProcessingResult(
            line_items=line_items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            confidence=confidence,
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
    
    def _calculate_confidence(
        self,
        subtotal: Optional[Decimal],
        tax: Optional[Decimal],
        total: Optional[Decimal],
        line_items: List[LineItem]
    ) -> float:
        """Calculate overall confidence in the pattern matching results.
        
        Args:
            subtotal: Detected subtotal amount
            tax: Detected tax amount
            total: Detected total amount
            line_items: List of detected line items
            
        Returns:
            Float confidence score between 0 and 1
        """
        confidence = 0.5  # Base confidence
        
        # Increase confidence based on presence of key components
        if subtotal is not None:
            confidence += 0.1
        if tax is not None:
            confidence += 0.1
        if total is not None:
            confidence += 0.1
        if line_items:
            confidence += 0.1
            
        # Check mathematical consistency if we have all components
        if all([subtotal, tax, total]):
            if abs(subtotal + tax - total) < Decimal('0.01'):
                confidence += 0.1
                
            # Additional check: line items sum should match subtotal
            if line_items:
                line_items_sum = sum(item.price.extended_price or Decimal('0') for item in line_items)
                if abs(line_items_sum - subtotal) < Decimal('0.01'):
                    confidence += 0.1
                
        return min(confidence, 1.0)

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
        """Process the receipt using progressive refinement for line items.
        
        Args:
            receipt: Receipt data model
            receipt_lines: List of receipt lines
            receipt_words: List of receipt words
            places_api_data: Optional Places API data for context

        Returns:
            LineItemAnalysis containing processed line items and metadata
        """
        # Validate inputs
        if receipt is None:
            raise ValueError("Receipt cannot be None")
        if receipt_lines is None:
            raise ValueError("Receipt lines cannot be None")
        if receipt_words is None:
            raise ValueError("Receipt words cannot be None")
        if not isinstance(receipt_lines, list):
            raise ValueError("Receipt lines must be a list")
        if not isinstance(receipt_words, list):
            raise ValueError("Receipt words must be a list")
        
        # Ensure places_api_data is a dict if provided
        if places_api_data is not None and not isinstance(places_api_data, dict):
            raise ValueError("places_api_data must be None or a dictionary")
        
        # Stage 1: Fast Pattern Matching
        logger.info("Stage 1: Performing fast pattern matching")
        initial_results = self.fast_processor.process(receipt, receipt_lines, receipt_words)
        
        # Stage 2: Confidence Assessment
        logger.info("Stage 2: Assessing confidence and identifying uncertain items")
        uncertain_items = self.validator.identify_uncertain_items(initial_results)
        
        # Stage 3: LLM Enhancement (if needed)
        final_results = initial_results
        if uncertain_items and self.llm_processor:
            logger.info("Stage 3: Enhancing results with LLM processing")
            try:
                # Convert initial results to a clean dictionary format
                initial_state = {
                    "line_items": [
                        {
                            "description": item.description,
                            "quantity": item.quantity.__dict__ if item.quantity else None,
                            "price": {
                                "unit_price": str(item.price.unit_price) if item.price and item.price.unit_price else None,
                                "extended_price": str(item.price.extended_price) if item.price and item.price.extended_price else None
                            } if item.price else None,
                            "confidence": item.confidence,
                            "line_ids": item.line_ids
                        }
                        for item in initial_results.line_items
                    ],
                    "subtotal": str(initial_results.subtotal) if initial_results.subtotal else None,
                    "tax": str(initial_results.tax) if initial_results.tax else None,
                    "total": str(initial_results.total) if initial_results.total else None,
                }
                
                enhanced_dict = await self.llm_processor.process_uncertain_items(
                    uncertain_items=uncertain_items,
                    receipt=receipt,
                    receipt_lines=receipt_lines,
                    receipt_words=receipt_words,
                    initial_state=initial_state,
                    places_api_data=places_api_data or {}
                )
                
                if enhanced_dict:
                    logger.debug(f"Enhanced results received: {enhanced_dict}")
                    # Convert dictionary results back to ProcessingResult
                    try:
                        # Convert line items from dict to LineItem objects if present
                        line_items = []
                        if "line_items" in enhanced_dict:
                            for item in enhanced_dict["line_items"]:
                                logger.debug(f"Processing line item: {item}")
                                
                                try:
                                    # If it's already a LineItem, use it directly
                                    if isinstance(item, LineItem):
                                        line_items.append(item)
                                        continue
                                        
                                    # If it's a dict, convert it
                                    if isinstance(item, dict):
                                        # Extract quantity
                                        quantity = None
                                        if isinstance(item.get("quantity"), dict):
                                            qty_dict = item["quantity"]
                                            quantity = Quantity(
                                                amount=Decimal(str(qty_dict["amount"])),
                                                unit=qty_dict.get("unit")
                                            )
                                        elif isinstance(item.get("quantity"), Quantity):
                                            quantity = item["quantity"]
                                        
                                        # Extract price
                                        price = None
                                        if isinstance(item.get("price"), dict):
                                            price_dict = item["price"]
                                            price = Price(
                                                unit_price=Decimal(str(price_dict["unit_price"])) if price_dict.get("unit_price") else None,
                                                extended_price=Decimal(str(price_dict["extended_price"])) if price_dict.get("extended_price") else None
                                            )
                                        elif isinstance(item.get("price"), Price):
                                            price = item["price"]
                                        
                                        # Create line item
                                        line_item = LineItem(
                                            description=str(item.get("description", "")),
                                            quantity=quantity,
                                            price=price,
                                            confidence=float(item.get("confidence", 0.7)),
                                            line_ids=list(item.get("line_ids", []))
                                        )
                                        line_items.append(line_item)
                                    else:
                                        logger.warning(
                                            f"Skipping line item with unexpected type: {type(item)}. "
                                            f"Expected LineItem or dict, got {type(item)}"
                                        )
                                        continue
                                        
                                except (KeyError, ValueError, TypeError, InvalidOperation) as e:
                                    logger.error(
                                        f"Error processing line item {item}: {str(e)}. "
                                        f"Type: {type(item)}"
                                    )
                                    continue
                        else:
                            line_items = initial_results.line_items

                        # Convert decimal values with proper error handling
                        try:
                            subtotal = Decimal(str(enhanced_dict["subtotal"])) if enhanced_dict.get("subtotal") else initial_results.subtotal
                        except (ValueError, InvalidOperation, TypeError):
                            logger.warning("Failed to convert subtotal, using initial value")
                            subtotal = initial_results.subtotal
                            
                        try:
                            tax = Decimal(str(enhanced_dict["tax"])) if enhanced_dict.get("tax") else initial_results.tax
                        except (ValueError, InvalidOperation, TypeError):
                            logger.warning("Failed to convert tax, using initial value")
                            tax = initial_results.tax
                            
                        try:
                            total = Decimal(str(enhanced_dict["total"])) if enhanced_dict.get("total") else initial_results.total
                        except (ValueError, InvalidOperation, TypeError):
                            logger.warning("Failed to convert total, using initial value")
                            total = initial_results.total
                        
                        # Create new ProcessingResult with converted values
                        final_results = ProcessingResult(
                            line_items=line_items,
                            subtotal=subtotal,
                            tax=tax,
                            total=total,
                            confidence=float(enhanced_dict.get("confidence", initial_results.confidence)),
                            uncertain_items=enhanced_dict.get("uncertain_items", [])
                        )
                    except Exception as e:
                        logger.error(f"Error converting LLM results to ProcessingResult: {str(e)}", exc_info=True)
                        # Keep initial results if conversion fails
                        final_results = initial_results
            except Exception as e:
                logger.error(f"Error in LLM processing: {str(e)}", exc_info=True)
                # Continue with initial results if LLM processing fails
        
        # Final validation
        validation_results = self.validator.validate_full_receipt(final_results)
        
        # Convert to LineItemAnalysis
        return LineItemAnalysis(
            items=final_results.line_items,
            total_found=len(final_results.line_items),
            subtotal=final_results.subtotal,
            tax=final_results.tax,
            total=final_results.total,
            discrepancies=[f"{type}: {msg}" for type, msg in 
                         ([(k, v) for k, v in validation_results.items() if k != 'status'])],
            confidence=final_results.confidence
        ) 