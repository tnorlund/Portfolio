"""
PII (Personally Identifiable Information) masking utility.

This module provides functions to detect and mask sensitive information
before sending data to external APIs like Google Places API or GPT.
"""

import re
import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class PIIType(Enum):
    """Types of PII that can be detected and masked."""
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    DRIVERS_LICENSE = "drivers_license"
    PASSPORT = "passport"
    BANK_ACCOUNT = "bank_account"
    PERSONAL_ID = "personal_id"
    EMAIL = "email"  # Only mask in certain contexts
    PHONE = "phone"  # Only mask in certain contexts
    NAME = "name"  # Only when not a business name


@dataclass
class PIIMatch:
    """Represents a detected PII match."""
    pii_type: PIIType
    original_text: str
    masked_text: str
    start_pos: Optional[int] = None
    end_pos: Optional[int] = None
    confidence: float = 1.0


class PIIMasker:
    """
    Masks personally identifiable information (PII) in text.
    
    Designed to scrub sensitive data before sending to external APIs
    while preserving business-relevant information like merchant names
    and addresses that are needed for processing.
    """
    
    def __init__(
        self,
        mask_emails: bool = True,
        mask_phones: bool = False,  # Usually needed for Places API
        custom_patterns: Optional[Dict[str, str]] = None
    ):
        """
        Initialize PII masker.
        
        Args:
            mask_emails: Whether to mask email addresses
            mask_phones: Whether to mask phone numbers (usually false for receipts)
            custom_patterns: Additional regex patterns to detect and mask
        """
        self.mask_emails = mask_emails
        self.mask_phones = mask_phones
        self.custom_patterns = custom_patterns or {}
        
        # Compile regex patterns for efficiency
        self._compile_patterns()
        
        # Known safe merchant names to avoid masking
        self.safe_merchant_patterns = {
            r"walmart", r"target", r"cvs", r"walgreens", r"mcdonald",
            r"starbucks", r"amazon", r"costco", r"kroger", r"safeway"
        }
    
    def _compile_patterns(self):
        """Compile regex patterns for PII detection."""
        self.patterns = {
            # Credit card numbers (with spaces or dashes)
            PIIType.CREDIT_CARD: re.compile(
                r'\b(?:\d{4}[\s\-]?){3}\d{4}\b'
            ),
            
            # Social Security Numbers
            PIIType.SSN: re.compile(
                r'\b\d{3}-\d{2}-\d{4}\b|\b\d{9}\b'
            ),
            
            # Common ID number patterns
            PIIType.DRIVERS_LICENSE: re.compile(
                r'\b[A-Z]\d{7,12}\b|\bDL[\s#:]*\d{7,12}\b',
                re.IGNORECASE
            ),
            
            # Passport numbers (various formats)
            PIIType.PASSPORT: re.compile(
                r'\b[A-Z]{1,2}\d{6,9}\b',
                re.IGNORECASE
            ),
            
            # Bank account numbers (IBAN or routing + account)
            PIIType.BANK_ACCOUNT: re.compile(
                r'\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b|'
                r'\b\d{9}\s*\d{8,17}\b',
                re.IGNORECASE
            ),
        }
        
        # Conditionally add email pattern
        if self.mask_emails:
            self.patterns[PIIType.EMAIL] = re.compile(
                r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            )
        
        # Conditionally add phone pattern
        if self.mask_phones:
            self.patterns[PIIType.PHONE] = re.compile(
                r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
            )
    
    def mask_text(self, text: str, context: Optional[Dict[str, Any]] = None) -> Tuple[str, List[PIIMatch]]:
        """
        Mask PII in text while preserving business information.
        
        Args:
            text: Text to mask
            context: Optional context to help determine what should be masked
            
        Returns:
            Tuple of (masked_text, list_of_matches)
        """
        if not text:
            return text, []
        
        matches = []
        masked_text = text
        
        # Check if this appears to be a business name (don't mask)
        if context and self._is_business_context(text, context):
            return text, []
        
        # Apply each pattern
        for pii_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                original = match.group()
                
                # Skip if this looks like a business identifier
                if self._is_safe_business_data(original, pii_type):
                    continue
                
                # Create masked version
                masked = self._create_mask(original, pii_type)
                
                # Record the match
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    original_text=original,
                    masked_text=masked,
                    start_pos=match.start(),
                    end_pos=match.end()
                ))
                
                # Replace in text
                masked_text = masked_text.replace(original, masked)
        
        return masked_text, matches
    
    def mask_receipt_words(
        self, 
        words: List[Dict[str, Any]], 
        preserve_business_data: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Mask PII in receipt words while preserving business-relevant information.
        
        Args:
            words: List of receipt word dictionaries
            preserve_business_data: Whether to preserve merchant names/addresses
            
        Returns:
            List of words with PII masked
        """
        masked_words = []
        
        for word in words:
            word_copy = word.copy()
            text = word.get("text", "")
            
            # Create context for this word
            context = {
                "word_position": word.get("word_id", 0),
                "line_position": word.get("line_id", 0),
                "is_merchant_area": word.get("line_id", 999) < 5,  # Top 5 lines
                "preserve_business": preserve_business_data
            }
            
            # Mask the text
            masked_text, matches = self.mask_text(text, context)
            
            if matches:
                word_copy["text"] = masked_text
                word_copy["pii_masked"] = True
                word_copy["pii_types"] = [m.pii_type.value for m in matches]
                logger.debug(f"Masked PII in word {word.get('word_id')}: {text} -> {masked_text}")
            
            masked_words.append(word_copy)
        
        return masked_words
    
    def mask_api_request(
        self,
        data: Dict[str, Any],
        api_type: str = "places"
    ) -> Tuple[Dict[str, Any], List[PIIMatch]]:
        """
        Mask PII in API request data based on API type.
        
        Args:
            data: API request data
            api_type: Type of API ("places", "gpt", etc.)
            
        Returns:
            Tuple of (masked_data, list_of_matches)
        """
        masked_data = data.copy()
        all_matches = []
        
        if api_type == "places":
            # For Places API, we typically need phone numbers and addresses
            # but should mask credit cards, SSNs, etc.
            fields_to_check = ["query", "input", "keyword"]
            
            for field in fields_to_check:
                if field in masked_data and isinstance(masked_data[field], str):
                    masked_text, matches = self.mask_text(
                        masked_data[field],
                        context={"api_type": "places", "field": field}
                    )
                    if matches:
                        masked_data[field] = masked_text
                        all_matches.extend(matches)
        
        elif api_type == "gpt":
            # For GPT, mask more aggressively
            def mask_recursive(obj):
                if isinstance(obj, str):
                    masked, matches = self.mask_text(obj, context={"api_type": "gpt"})
                    all_matches.extend(matches)
                    return masked
                elif isinstance(obj, dict):
                    return {k: mask_recursive(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [mask_recursive(item) for item in obj]
                return obj
            
            masked_data = mask_recursive(masked_data)
        
        return masked_data, all_matches
    
    def _is_business_context(self, text: str, context: Dict[str, Any]) -> bool:
        """Determine if text is likely business information rather than PII."""
        # Check if we should preserve business data
        if not context.get("preserve_business", True):
            return False
        
        # Check if this is in merchant area (top of receipt)
        if context.get("is_merchant_area", False):
            return True
        
        # Check against known merchant patterns
        text_lower = text.lower()
        for pattern in self.safe_merchant_patterns:
            if re.search(pattern, text_lower):
                return True
        
        return False
    
    def _is_safe_business_data(self, text: str, pii_type: PIIType) -> bool:
        """Check if detected pattern is actually safe business data."""
        # Phone numbers are often business phones on receipts
        if pii_type == PIIType.PHONE and not self.mask_phones:
            return True
        
        # Some "credit card" patterns might be product codes
        if pii_type == PIIType.CREDIT_CARD:
            # Check if it's preceded by common product indicators
            product_indicators = ["sku", "upc", "item", "product", "code"]
            text_lower = text.lower()
            for indicator in product_indicators:
                if indicator in text_lower:
                    return True
        
        return False
    
    def _create_mask(self, text: str, pii_type: PIIType) -> str:
        """Create masked version of text based on PII type."""
        if pii_type == PIIType.CREDIT_CARD:
            # Keep last 4 digits for credit cards
            if len(text) >= 4:
                return "****-****-****-" + text[-4:]
            return "****"
        
        elif pii_type == PIIType.SSN:
            return "***-**-****"
        
        elif pii_type == PIIType.EMAIL:
            # Keep domain for emails
            parts = text.split('@')
            if len(parts) == 2:
                return "****@" + parts[1]
            return "****@****.com"
        
        elif pii_type == PIIType.PHONE:
            # Keep area code for phones
            digits = re.sub(r'\D', '', text)
            if len(digits) >= 10:
                return f"({digits[:3]}) ***-****"
            return "(***) ***-****"
        
        else:
            # Generic masking for other types
            return "*" * len(text)
    
    def get_statistics(self, matches: List[PIIMatch]) -> Dict[str, Any]:
        """Get statistics about PII matches."""
        stats = {
            "total_matches": len(matches),
            "by_type": {},
            "high_confidence": 0
        }
        
        for match in matches:
            pii_type = match.pii_type.value
            stats["by_type"][pii_type] = stats["by_type"].get(pii_type, 0) + 1
            
            if match.confidence >= 0.9:
                stats["high_confidence"] += 1
        
        return stats


def create_receipt_masker() -> PIIMasker:
    """
    Create a PII masker configured for receipt processing.
    
    This configuration preserves business-relevant information while
    masking true PII.
    """
    return PIIMasker(
        mask_emails=True,  # Mask personal emails
        mask_phones=False,  # Keep business phone numbers
        custom_patterns={
            # Add any receipt-specific patterns here
        }
    )


def mask_before_api_call(
    data: Any,
    api_type: str = "places",
    masker: Optional[PIIMasker] = None
) -> Tuple[Any, List[PIIMatch]]:
    """
    Convenience function to mask data before API calls.
    
    Args:
        data: Data to mask (dict, list, or string)
        api_type: Type of API being called
        masker: Optional custom masker instance
        
    Returns:
        Tuple of (masked_data, list_of_matches)
    """
    if masker is None:
        masker = create_receipt_masker()
    
    if isinstance(data, str):
        return masker.mask_text(data)
    elif isinstance(data, dict):
        return masker.mask_api_request(data, api_type)
    elif isinstance(data, list):
        # Assume list of receipt words
        masked_words = masker.mask_receipt_words(data)
        # Extract matches from masked words
        matches = []
        for word in masked_words:
            if word.get("pii_masked"):
                # Create synthetic matches for reporting
                for pii_type in word.get("pii_types", []):
                    matches.append(PIIMatch(
                        pii_type=PIIType(pii_type),
                        original_text="[REDACTED]",
                        masked_text=word["text"]
                    ))
        return masked_words, matches
    else:
        return data, []