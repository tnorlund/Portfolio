"""
Enhanced pattern analyzer for line item detection without GPT dependency.

This module implements sophisticated pattern matching and spatial analysis
to classify currency amounts and extract line items from receipts without
needing expensive GPT calls.
"""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Set, Tuple

from ..models.line_item import LineItem, Price, Quantity

logger = logging.getLogger(__name__)


@dataclass
class EnhancedCurrencyContext:
    """Enhanced context for currency classification."""
    
    amount: Decimal
    text: str
    line_id: str
    word_id: Optional[int]
    x_position: float
    y_position: float
    full_line: str
    left_text: str
    line_position_pct: float  # 0.0 = top of receipt, 1.0 = bottom
    has_quantity_pattern: bool
    quantity_info: Optional[Dict] = None
    nearby_keywords: Set[str] = None
    classification: Optional[str] = None
    confidence: float = 0.0
    

class QuantityPatternMatcher:
    """Enhanced quantity pattern matching."""
    
    # Quantity patterns with named groups for extraction
    QUANTITY_PATTERNS = [
        # "2 @ $5.99" or "2 @ 5.99"
        (r'(?P<qty>\d+(?:\.\d+)?)\s*@\s*\$?(?P<price>\d+(?:\.\d+)?)', 'at_symbol'),
        # "2 x $5.99" or "2 X 5.99"
        (r'(?P<qty>\d+(?:\.\d+)?)\s*[xX]\s*\$?(?P<price>\d+(?:\.\d+)?)', 'x_symbol'),
        # "Qty: 2" or "QTY 2"
        (r'(?:qty|quantity)[:.]?\s*(?P<qty>\d+(?:\.\d+)?)', 'qty_prefix'),
        # "2 lb" or "2.5 kg"
        (r'(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>lb|lbs|kg|kgs|oz|g|grams?|liters?|l|ml|gal|gallons?)', 'weight_volume'),
        # "2 EA" (each)
        (r'(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>ea|each|pcs?|pieces?|items?|units?)', 'count_unit'),
        # Multi-line pattern: quantity on previous line
        (r'^(?P<qty>\d+)$', 'standalone_number'),  # Just a number on its own line
    ]
    
    def __init__(self):
        self.compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), pattern_type) 
            for pattern, pattern_type in self.QUANTITY_PATTERNS
        ]
    
    def find_quantity_patterns(self, text: str) -> Optional[Dict]:
        """Find quantity patterns in text."""
        text = text.strip()
        
        for pattern, pattern_type in self.compiled_patterns:
            match = pattern.search(text)
            if match:
                result = {
                    'type': pattern_type,
                    'matched_text': match.group(0),
                    'groups': match.groupdict()
                }
                
                # Extract quantity value
                if 'qty' in result['groups']:
                    try:
                        result['quantity'] = Decimal(result['groups']['qty'])
                    except (ValueError, InvalidOperation):
                        continue
                
                # Extract unit price if present
                if 'price' in result['groups']:
                    try:
                        result['unit_price'] = Decimal(result['groups']['price'])
                    except (ValueError, InvalidOperation):
                        pass
                
                # Extract unit if present
                if 'unit' in result['groups']:
                    result['unit'] = result['groups']['unit']
                
                return result
        
        return None


class SpatialAnalyzer:
    """Analyzes spatial relationships between receipt elements."""
    
    # Financial keywords grouped by type (with OCR error variants)
    FINANCIAL_KEYWORDS = {
        'subtotal': {
            'subtotal', 'sub total', 'sub-total', 'net total', 'net amount', 'merchandise',
            'subtot', 'sub tot', 'subt', 'nettotal', 'netamt'  # OCR variants
        },
        'tax': {
            'tax', 'vat', 'gst', 'hst', 'sales tax', 'state tax', 'local tax', 'city tax',
            'ta', 'tx', 'salestax', 'statetax'  # OCR variants
        },
        'total': {
            'total', 'balance due', 'amount due', 'grand total', 'payment due', 'due', 
            'pay', 'balance', 'payment total', 'order total', 'final amount', 'to pay',
            'please pay', 'amount', 'charge', 'sum', 'final',
            'tot', 'totl', 'grandtotal', 'paymenttotal', 'ordertotal', 'amountdue',  # OCR variants
            'balancedue', 'grandtot', 'finaltotal'
        },
        'discount': {
            'discount', 'coupon', 'sale', 'markdown', 'promo', 'savings', 'off', 'save',
            'disc', 'discnt', 'coup', 'prm'  # OCR variants
        },
        'fee': {
            'fee', 'service charge', 'delivery', 'tip', 'gratuity', 'surcharge',
            'svc', 'srvc', 'deliv', 'chg', 'charge'  # OCR variants
        },
    }
    
    # Product/item related keywords
    ITEM_KEYWORDS = {
        'product', 'item', 'qty', 'quantity', 'ea', 'each', 'lb', 'kg', 'oz',
        'price', 'cost', 'amount', '@', 'x'
    }
    
    @classmethod
    def analyze_line_position(cls, y_position: float, total_height: float) -> float:
        """Calculate relative position on receipt (0.0 = top, 1.0 = bottom)."""
        if total_height <= 0:
            return 0.5
        return min(1.0, max(0.0, y_position / total_height))
    
    @classmethod
    def find_nearby_keywords(cls, text: str, surrounding_text: str = "") -> Tuple[Set[str], Dict[str, float]]:
        """Find financial and item keywords in text."""
        combined_text = f"{text} {surrounding_text}".lower()
        found_keywords = set()
        keyword_scores = {}
        
        # Check for financial keywords
        for category, keywords in cls.FINANCIAL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in combined_text:
                    found_keywords.add(category)
                    # Higher score for exact matches in main text
                    if keyword in text.lower():
                        # Don't double-count keywords
                        if category not in keyword_scores:
                            keyword_scores[category] = 1.0
                    elif keyword in surrounding_text.lower():
                        if category not in keyword_scores:
                            keyword_scores[category] = 0.5
        
        # Check for fuzzy keyword matches to handle OCR errors
        cls._check_fuzzy_keywords(combined_text, found_keywords, keyword_scores)
        
        # Check for item keywords
        item_score = 0
        for keyword in cls.ITEM_KEYWORDS:
            if keyword in combined_text:
                item_score += 0.3
        if item_score > 0:
            found_keywords.add('item')
            keyword_scores['item'] = item_score
        
        return found_keywords, keyword_scores
    
    @classmethod
    def _check_fuzzy_keywords(cls, text: str, found_keywords: Set[str], keyword_scores: Dict[str, float]):
        """Check for fuzzy keyword matches to handle OCR errors."""
        # Check for partial matches for important keywords
        fuzzy_patterns = {
            'total': ['utal', 'otal', 'ttal', 'toal', 'totl'],  # Missing letters
            'tax': ['ta', 'tx', 'ax'],  # Very short OCR errors
            'subtotal': ['subtot', 'subt', 'sub'],  # Truncated
        }
        
        for category, patterns in fuzzy_patterns.items():
            for pattern in patterns:
                if pattern in text and len(pattern) >= 2:  # Minimum 2 chars
                    found_keywords.add(category)
                    if category not in keyword_scores:
                        keyword_scores[category] = 0.6  # Lower confidence for fuzzy
    
    @classmethod
    def group_by_lines(cls, currency_contexts: List[EnhancedCurrencyContext]) -> Dict[str, List[EnhancedCurrencyContext]]:
        """Group currency contexts by line ID."""
        lines = {}
        for ctx in currency_contexts:
            if ctx.line_id not in lines:
                lines[ctx.line_id] = []
            lines[ctx.line_id].append(ctx)
        return lines


class EnhancedPatternAnalyzer:
    """
    Analyzes currency amounts using enhanced pattern matching and spatial analysis
    to classify them without needing GPT.
    """
    
    def __init__(self):
        self.quantity_matcher = QuantityPatternMatcher()
        self.spatial_analyzer = SpatialAnalyzer()
    
    def analyze_currency_contexts(
        self, 
        currency_contexts: List[Dict],
        receipt_height: Optional[float] = None
    ) -> Dict:
        """
        Analyze currency contexts to classify them and extract line items.
        
        This replaces the GPT spatial currency analysis with pattern-based logic.
        """
        if not currency_contexts:
            return {
                'line_items': [],
                'financial_summary': {
                    'subtotal': None,
                    'tax': None,
                    'total': None
                },
                'classification': [],
                'confidence': 0.0
            }
        
        # Convert to enhanced contexts
        enhanced_contexts = []
        
        # Determine receipt height if not provided
        if receipt_height is None and currency_contexts:
            receipt_height = max(ctx.get('y_position', 0) for ctx in currency_contexts)
            if receipt_height == 0:
                receipt_height = 1000  # Default assumption
        
        # Enhance each context with additional analysis
        for ctx in currency_contexts:
            enhanced = self._enhance_currency_context(ctx, receipt_height)
            enhanced_contexts.append(enhanced)
        
        # Classify currency amounts
        classifications = self._classify_currency_amounts(enhanced_contexts)
        
        # Extract line items
        line_items = self._extract_line_items(enhanced_contexts, classifications)
        
        # Extract financial summary
        financial_summary = self._extract_financial_summary(enhanced_contexts, classifications)
        
        # Calculate overall confidence
        confidence = self._calculate_confidence(classifications)
        
        return {
            'line_items': line_items,
            'financial_summary': financial_summary,
            'classification': classifications,
            'confidence': confidence
        }
    
    def _enhance_currency_context(self, ctx: Dict, receipt_height: float) -> EnhancedCurrencyContext:
        """Enhance a currency context with additional analysis."""
        # Extract basic info
        amount = Decimal(str(ctx.get('amount', 0)))
        text = ctx.get('text', '')
        line_id = ctx.get('line_id', '0')
        x_position = ctx.get('x_position', 0)
        y_position = ctx.get('y_position', 0)
        full_line = ctx.get('full_line', '')
        left_text = ctx.get('left_text', '')
        
        # Calculate line position percentage
        line_position_pct = SpatialAnalyzer.analyze_line_position(y_position, receipt_height)
        
        # Check for quantity patterns
        quantity_info = None
        has_quantity_pattern = False
        
        # Check in left text first
        if left_text:
            quantity_info = self.quantity_matcher.find_quantity_patterns(left_text)
            if quantity_info:
                has_quantity_pattern = True
        
        # Check in full line if not found
        if not quantity_info and full_line:
            quantity_info = self.quantity_matcher.find_quantity_patterns(full_line)
            if quantity_info:
                has_quantity_pattern = True
        
        # Find nearby keywords
        nearby_keywords, keyword_scores = SpatialAnalyzer.find_nearby_keywords(
            full_line, 
            left_text
        )
        
        return EnhancedCurrencyContext(
            amount=amount,
            text=text,
            line_id=line_id,
            word_id=ctx.get('word_id'),
            x_position=x_position,
            y_position=y_position,
            full_line=full_line,
            left_text=left_text,
            line_position_pct=line_position_pct,
            has_quantity_pattern=has_quantity_pattern,
            quantity_info=quantity_info,
            nearby_keywords=nearby_keywords
        )
    
    def _classify_currency_amounts(self, contexts: List[EnhancedCurrencyContext]) -> List[Dict]:
        """Classify currency amounts based on patterns and position."""
        classifications = []
        
        # Sort by amount descending to help identify totals
        sorted_contexts = sorted(contexts, key=lambda x: x.amount, reverse=True)
        
        # Track what we've found
        found_total = False
        found_subtotal = False
        found_tax = False
        
        for ctx in sorted_contexts:
            classification = self._classify_single_amount(
                ctx, 
                found_total=found_total,
                found_subtotal=found_subtotal,
                found_tax=found_tax,
                all_contexts=contexts
            )
            
            classifications.append({
                'amount': float(ctx.amount),
                'line_id': ctx.line_id,
                'category': classification['category'],
                'confidence': classification['confidence'],
                'reasoning': classification['reasoning']
            })
            
            # Update tracking
            if classification['category'] == 'TOTAL':
                found_total = True
            elif classification['category'] == 'SUBTOTAL':
                found_subtotal = True
            elif classification['category'] == 'TAX':
                found_tax = True
        
        return classifications
    
    def _classify_single_amount(
        self, 
        ctx: EnhancedCurrencyContext,
        found_total: bool,
        found_subtotal: bool,
        found_tax: bool,
        all_contexts: List[EnhancedCurrencyContext]
    ) -> Dict:
        """Classify a single currency amount."""
        # Start with base confidence
        confidence = 0.5
        reasoning = []
        category = 'LINE_ITEM'  # Default
        
        # Rule 1: Strong keyword matches
        if 'total' in ctx.nearby_keywords and not found_total:
            # Check if it's likely the grand total
            if ctx.line_position_pct > 0.7:  # Bottom 30% of receipt
                category = 'TOTAL'
                confidence = 0.9
                reasoning.append("Found 'total' keyword in bottom portion of receipt")
            elif 'grand' in ctx.full_line.lower() or 'final' in ctx.full_line.lower():
                category = 'TOTAL'
                confidence = 0.85
                reasoning.append("Found 'grand total' or 'final' keyword")
            elif 'subtotal' not in ctx.full_line.lower():
                category = 'TOTAL'
                confidence = 0.75
                reasoning.append("Found 'total' keyword")
        
        elif 'subtotal' in ctx.nearby_keywords and not found_subtotal:
            category = 'SUBTOTAL'
            confidence = 0.85
            reasoning.append("Found 'subtotal' keyword")
        
        elif 'tax' in ctx.nearby_keywords and not found_tax:
            category = 'TAX'
            confidence = 0.85
            reasoning.append("Found 'tax' keyword")
            
            # Validate tax amount is reasonable (typically 3-15% of subtotal)
            if found_subtotal:
                subtotal_amt = self._find_amount_by_category(all_contexts, 'SUBTOTAL')
                if subtotal_amt:
                    tax_rate = ctx.amount / subtotal_amt
                    if 0.03 <= tax_rate <= 0.15:
                        confidence = 0.95
                        reasoning.append(f"Tax rate {tax_rate:.1%} is reasonable")
                    else:
                        confidence = 0.6
                        reasoning.append(f"Tax rate {tax_rate:.1%} seems unusual")
        
        elif 'discount' in ctx.nearby_keywords:
            category = 'DISCOUNT'
            confidence = 0.8
            reasoning.append("Found 'discount' keyword")
        
        elif 'fee' in ctx.nearby_keywords:
            category = 'FEE'
            confidence = 0.8
            reasoning.append("Found 'fee' keyword")
        
        # Rule 2: Position-based classification
        elif ctx.line_position_pct > 0.8 and not found_total:
            # Very bottom of receipt, likely total if it's the largest amount
            if ctx.amount == max(c.amount for c in all_contexts):
                category = 'TOTAL'
                confidence = 0.7
                reasoning.append("Largest amount at bottom of receipt")
        
        # Rule 3: Quantity patterns indicate line items
        if ctx.has_quantity_pattern and category == 'LINE_ITEM':
            confidence = 0.9
            reasoning.append("Has quantity pattern")
            
            # If we have a unit price, even more confident
            if ctx.quantity_info and 'unit_price' in ctx.quantity_info:
                confidence = 0.95
                reasoning.append("Has unit price pattern")
        
        # Rule 4: Item keywords
        elif 'item' in ctx.nearby_keywords and category == 'LINE_ITEM':
            confidence = 0.7
            reasoning.append("Has item-related keywords")
        
        # Rule 5: Heuristic for line items
        elif category == 'LINE_ITEM':
            # Line items typically in middle of receipt
            if 0.2 <= ctx.line_position_pct <= 0.7:
                confidence = 0.6
                reasoning.append("In typical line item area of receipt")
            else:
                confidence = 0.4
                reasoning.append("Default classification")
        
        return {
            'category': category,
            'confidence': confidence,
            'reasoning': '; '.join(reasoning) if reasoning else 'No specific patterns found'
        }
    
    def _find_amount_by_category(self, contexts: List[EnhancedCurrencyContext], category: str) -> Optional[Decimal]:
        """Find the amount for a specific category."""
        for ctx in contexts:
            if ctx.classification == category:
                return ctx.amount
        return None
    
    def _extract_line_items(
        self, 
        contexts: List[EnhancedCurrencyContext], 
        classifications: List[Dict]
    ) -> List[Dict]:
        """Extract line items from classified contexts."""
        line_items = []
        
        # Create a mapping from amount to classification for faster lookup
        amount_to_classification = {}
        for classification in classifications:
            amount = classification['amount']
            amount_to_classification[amount] = classification
        
        # Process contexts in original order (not sorted by amount)
        for ctx in contexts:
            # Find classification for this context
            classification = amount_to_classification.get(float(ctx.amount))
            if not classification:
                continue
                
            # Only process LINE_ITEM categories
            if classification['category'] != 'LINE_ITEM':
                continue
            
            # Build line item
            item = {
                'amount': float(ctx.amount),
                'description': ctx.left_text.strip() if ctx.left_text else f"Item {ctx.line_id}",
                'line_id': ctx.line_id,
                'confidence': classification['confidence']
            }
            
            # Add quantity information if available
            if ctx.quantity_info:
                if 'quantity' in ctx.quantity_info:
                    item['quantity'] = float(ctx.quantity_info['quantity'])
                if 'unit' in ctx.quantity_info:
                    item['unit'] = ctx.quantity_info['unit']
                if 'unit_price' in ctx.quantity_info:
                    item['unit_price'] = float(ctx.quantity_info['unit_price'])
            
            line_items.append(item)
        
        return line_items
    
    def _extract_financial_summary(
        self, 
        contexts: List[EnhancedCurrencyContext], 
        classifications: List[Dict]
    ) -> Dict:
        """Extract financial summary fields."""
        summary = {
            'subtotal': None,
            'tax': None,
            'total': None,
            'discount': None,
            'fees': None
        }
        
        # Create a mapping from amount to classification for faster lookup
        amount_to_classification = {}
        for classification in classifications:
            amount = classification['amount']
            amount_to_classification[amount] = classification
        
        # Process contexts to extract financial fields
        for ctx in contexts:
            # Find classification for this context
            classification = amount_to_classification.get(float(ctx.amount))
            if not classification:
                continue
                
            category = classification['category']
            
            if category == 'SUBTOTAL' and summary['subtotal'] is None:
                summary['subtotal'] = float(ctx.amount)
            elif category == 'TAX' and summary['tax'] is None:
                summary['tax'] = float(ctx.amount)
            elif category == 'TOTAL' and summary['total'] is None:
                summary['total'] = float(ctx.amount)
            elif category == 'DISCOUNT':
                if summary['discount'] is None:
                    summary['discount'] = 0
                summary['discount'] += float(ctx.amount)
            elif category == 'FEE':
                if summary['fees'] is None:
                    summary['fees'] = 0
                summary['fees'] += float(ctx.amount)
        
        # Validate and potentially infer missing values
        if summary['total'] is None and summary['subtotal'] is not None:
            # Try to calculate total
            calculated_total = summary['subtotal']
            if summary['tax'] is not None:
                calculated_total += summary['tax']
            if summary['fees'] is not None:
                calculated_total += summary['fees']
            if summary['discount'] is not None:
                calculated_total -= summary['discount']
            
            # Check if any context amount matches this calculated total
            for ctx in contexts:
                if abs(float(ctx.amount) - calculated_total) < 0.01:
                    summary['total'] = calculated_total
                    break
        
        return summary
    
    def _calculate_confidence(self, classifications: List[Dict]) -> float:
        """Calculate overall confidence score."""
        if not classifications:
            return 0.0
        
        # Weight different categories differently
        category_weights = {
            'TOTAL': 2.0,
            'SUBTOTAL': 1.5,
            'TAX': 1.5,
            'LINE_ITEM': 1.0,
            'DISCOUNT': 1.0,
            'FEE': 1.0
        }
        
        weighted_sum = 0
        total_weight = 0
        
        for classification in classifications:
            category = classification['category']
            confidence = classification['confidence']
            weight = category_weights.get(category, 1.0)
            
            weighted_sum += confidence * weight
            total_weight += weight
        
        return weighted_sum / total_weight if total_weight > 0 else 0.0


def enhanced_pattern_analysis(currency_contexts: List[Dict]) -> Dict:
    """
    Main entry point for enhanced pattern analysis.
    
    This function replaces gpt_request_spatial_currency_analysis.
    """
    analyzer = EnhancedPatternAnalyzer()
    return analyzer.analyze_currency_contexts(currency_contexts)