"""Receipt field extraction utilities for merchant validation."""

from dataclasses import dataclass, field
from typing import List

from receipt_dynamo.entities import ReceiptWord


@dataclass
class ExtractedMerchantFields:
    """Dataclass for extracted merchant fields from a receipt."""

    name: str = ""
    address: str = ""
    phone_number: str = ""
    emails: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for backward compatibility."""
        return {
            "name": self.name,
            "address": self.address,
            "phone_number": self.phone_number,
            "emails": self.emails,
            "urls": self.urls,
        }


def extract_candidate_merchant_fields(
    words: List[ReceiptWord],
) -> ExtractedMerchantFields:
    """
    Extract candidate merchant fields (name, address, phone) from receipt words.

    Args:
        words: List of ReceiptWord objects from the receipt

    Returns:
        ExtractedMerchantFields dataclass containing extracted fields
    """
    result = ExtractedMerchantFields()

    # Use sets to avoid duplicates from extracted_data
    address_extractions = set()
    phone_extractions = set()
    email_extractions = set()
    url_extractions = set()
    
    # Use lists for label-based words (these should be joined)
    name_words = []
    address_label_words = []
    phone_label_words = []

    for word in words:
        # Get the best value for this word - use extracted value if available, otherwise raw text
        word_value = word.text
        extracted_type = None
        extracted_value = None
        
        # Check if word has extracted_data with a value
        if hasattr(word, "extracted_data") and word.extracted_data:
            if isinstance(word.extracted_data, dict) and "value" in word.extracted_data:
                extracted_value = str(word.extracted_data["value"])
                extracted_type = word.extracted_data.get("type", "").lower()
        
        # Use extracted_data type to categorize if available (add to sets to deduplicate)
        if extracted_type and extracted_value:
            if extracted_type == "address":
                address_extractions.add(extracted_value)
            elif extracted_type == "phone":
                phone_extractions.add(extracted_value)
            elif extracted_type == "email":
                email_extractions.add(extracted_value)
            elif extracted_type in ["url", "website"]:
                url_extractions.add(extracted_value)
            # Note: dates and other types are not needed for merchant validation
        
        # Also check word labels for semantic meaning (if no extracted_data or for additional context)
        if hasattr(word, "labels") and word.labels:
            for label in word.labels:
                label_lower = label.lower()
                # Only add label-based words if not already covered by extraction
                if "address" in label_lower and not extracted_type:
                    address_label_words.append(word.text)
                elif "phone" in label_lower and not extracted_type:
                    phone_label_words.append(word.text)
                elif "email" in label_lower and extracted_type != "email":
                    email_extractions.add(word.text)
                elif ("url" in label_lower or "website" in label_lower) and extracted_type not in ["url", "website"]:
                    url_extractions.add(word.text)
                elif ("merchant" in label_lower or "business" in label_lower):
                    name_words.append(word.text)

        # Note: Word tags have been removed from the system
        # Labels are now used instead, which are already processed above

    # Combine extracted data with label-based data
    # For name: use label words
    if name_words:
        result.name = " ".join(name_words).strip()

    # For address: prefer extracted data, fall back to label words
    if address_extractions:
        # Join multiple addresses if found (rare)
        result.address = " ".join(sorted(address_extractions)).strip()
    elif address_label_words:
        result.address = " ".join(address_label_words).strip()

    # For phone: prefer extracted data, fall back to label words
    if phone_extractions:
        # Join multiple phones if found (rare)
        result.phone_number = " ".join(sorted(phone_extractions)).strip()
    elif phone_label_words:
        result.phone_number = " ".join(phone_label_words).strip()

    # Emails and URLs are already deduplicated via sets
    result.emails = list(email_extractions)
    result.urls = list(url_extractions)

    # If no labeled merchant name found, try to infer from top words
    if not result.name and words:
        # Take the first few words as potential merchant name
        top_words = []
        for word in words[:5]:
            # Use extracted value if available
            word_value = word.text
            if hasattr(word, "extracted_data") and word.extracted_data:
                if isinstance(word.extracted_data, dict) and "value" in word.extracted_data:
                    word_value = str(word.extracted_data["value"])
            
            if word_value and word_value.strip():
                top_words.append(word_value)
        
        if top_words:
            result.name = " ".join(top_words).strip()

    return result
