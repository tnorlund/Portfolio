from typing import Dict, List, Optional, Tuple
from decimal import Decimal
import logging
import re
from dataclasses import dataclass
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from ..models.line_item import LineItem, LineItemAnalysis, Price, Quantity
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
    uncertain_items: List[Dict]

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
                except (ValueError, DecimalException):
                    logger.debug(f"Failed to parse amount from {match.group(0)}")
                    continue
        
        return matches

    def process(self, receipt: Receipt, receipt_lines: List[ReceiptLine], receipt_words: List[ReceiptWord]) -> ProcessingResult:
        """Perform fast initial processing of the receipt."""
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
                    except (ValueError, IndexError, DecimalException):
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
            if 'total' in line_text and amounts:
                if 'subtotal' in line_text:
                    subtotal = amounts[-1].amount
                elif 'tax' in line_text:
                    tax = amounts[-1].amount
                else:  # If it's just "total", set it regardless of number of amounts
                    total = amounts[-1].amount
            
            # Mark lines with multiple amounts as uncertain
            if len(amounts) > 1:
                uncertain_items.append({
                    "line_id": line_id,
                    "text": line_text,
                    "amounts": [m.amount for m in amounts],
                    "reason": "multiple_amounts"
                })
        
        return ProcessingResult(
            line_items=line_items,
            subtotal=subtotal,
            tax=tax,
            total=total,
            confidence=0.7,
            uncertain_items=uncertain_items
        )

class ProgressiveValidator:
    """Validates receipt data and identifies uncertain items."""
    
    def identify_uncertain_items(self, result: ProcessingResult) -> List[Dict]:
        """Identify items that need additional processing."""
        uncertain_items = result.uncertain_items.copy()
        
        # Check for missing components
        if result.subtotal is None:
            uncertain_items.append({
                "type": "missing_component",
                "component": "subtotal"
            })
        if result.tax is None:
            uncertain_items.append({
                "type": "missing_component",
                "component": "tax"
            })
        if result.total is None:
            uncertain_items.append({
                "type": "missing_component",
                "component": "total"
            })
        
        # Validate totals
        if result.subtotal and result.line_items:
            calculated_subtotal = sum(item.price.extended_price or Decimal('0') 
                                   for item in result.line_items)
            if abs(calculated_subtotal - result.subtotal) > Decimal('0.01'):
                uncertain_items.append({
                    "type": "total_mismatch",
                    "calculated": str(calculated_subtotal),
                    "found": str(result.subtotal)
                })
        
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
        
        # Validate calculations
        if result.subtotal and result.tax and result.total:
            calculated_total = result.subtotal + result.tax
            if abs(calculated_total - result.total) > Decimal('0.01'):
                validation_results["errors"].append(
                    f"Total mismatch: calculated {calculated_total} != found {result.total}"
                )
        
        # Update status based on errors
        if validation_results["errors"]:
            validation_results["status"] = "invalid"
        elif validation_results["warnings"]:
            validation_results["status"] = "valid_with_warnings"
        
        return validation_results

class ProgressiveReceiptProcessor:
    """Main processor implementing the progressive refinement pipeline."""
    
    def __init__(self, gpt_api_key: Optional[str] = None):
        self.fast_processor = FastPatternMatcher()
        self.validator = ProgressiveValidator()
        self.llm_processor = LLMProcessor(gpt_api_key)
        self.gpt_api_key = gpt_api_key
    
    async def process_receipt(
        self,
        receipt: Receipt,
        receipt_lines: List[ReceiptLine],
        receipt_words: List[ReceiptWord],
        places_api_data: Optional[Dict] = None,
    ) -> LineItemAnalysis:
        """Process the receipt using the progressive refinement pipeline."""
        
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
        if uncertain_items:
            logger.info(f"Stage 3: Processing {len(uncertain_items)} uncertain items with LLM")
            
            # Prepare initial state dict with proper None handling
            initial_state = {
                "line_items": [item.__dict__ for item in initial_results.line_items],
                "subtotal": initial_results.subtotal,
                "tax": initial_results.tax,
                "total": initial_results.total,
            }
            
            try:
                updates = await self.llm_processor.process_uncertain_items(
                    uncertain_items,
                    receipt,
                    receipt_lines,
                    receipt_words,
                    initial_state,
                    places_api_data or {}  # Ensure we pass an empty dict if None
                )
                
                # Apply updates if we got them
                if updates:
                    logger.info("Applying LLM updates to results")
                    if "line_items" in updates and updates["line_items"]:
                        # Replace line items that were uncertain
                        updated_line_ids = set()
                        for item in updates["line_items"]:
                            if hasattr(item, 'line_ids'):
                                updated_line_ids.update(item.line_ids)
                        
                        # Keep items that weren't updated
                        kept_items = [
                            item for item in initial_results.line_items
                            if not any(line_id in updated_line_ids for line_id in item.line_ids)
                        ]
                        
                        final_results.line_items = kept_items + updates["line_items"]
                        if updates["line_items"]:
                            final_results.confidence = max(
                                initial_results.confidence,
                                sum(item.confidence for item in updates["line_items"]) / len(updates["line_items"])
                            )
                    
                    if "subtotal" in updates:
                        final_results.subtotal = updates["subtotal"]
                    if "tax" in updates:
                        final_results.tax = updates["tax"]
                    if "total" in updates:
                        final_results.total = updates["total"]
            except Exception as e:
                logger.error(f"Error in LLM processing: {str(e)}")
                # Continue with initial results if LLM processing fails
                logger.info("Continuing with initial results due to LLM processing error")
        
        # Stage 4: Final Validation
        logger.info("Stage 4: Performing final validation")
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