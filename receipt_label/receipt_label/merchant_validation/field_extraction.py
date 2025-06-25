"""Receipt field extraction utilities for merchant validation."""

from typing import List

from receipt_dynamo.entities import ReceiptWord


def extract_candidate_merchant_fields(words: List[ReceiptWord]) -> dict:
    """
    Extract candidate merchant fields (name, address, phone) from receipt words.

    Args:
        words: List of ReceiptWord objects from the receipt

    Returns:
        Dictionary containing extracted fields:
        {
            "name": str,
            "address": str,
            "phone_number": str,
            "emails": List[str],
            "urls": List[str]
        }
    """
    result = {
        "name": "",
        "address": "",
        "phone_number": "",
        "emails": [],
        "urls": [],
    }

    # Group words by their labels/tags
    address_words = []
    phone_words = []
    email_words = []
    url_words = []
    name_words = []

    for word in words:
        # Check word labels for semantic meaning
        if hasattr(word, "labels") and word.labels:
            for label in word.labels:
                if "address" in label.lower():
                    address_words.append(word.text)
                elif "phone" in label.lower():
                    phone_words.append(word.text)
                elif "email" in label.lower():
                    email_words.append(word.text)
                elif "url" in label.lower() or "website" in label.lower():
                    url_words.append(word.text)
                elif "merchant" in label.lower() or "business" in label.lower():
                    name_words.append(word.text)

        # Check word tags for additional context
        if hasattr(word, "tags") and word.tags:
            for tag in word.tags:
                tag_value = tag.tag if hasattr(tag, "tag") else str(tag)
                if tag_value.lower() in ["address", "location"]:
                    address_words.append(word.text)
                elif tag_value.lower() in ["phone", "telephone"]:
                    phone_words.append(word.text)
                elif tag_value.lower() in ["email"]:
                    email_words.append(word.text)
                elif tag_value.lower() in ["url", "website"]:
                    url_words.append(word.text)
                elif tag_value.lower() in ["merchant_name", "business_name"]:
                    name_words.append(word.text)

    # Join extracted words into strings
    if name_words:
        result["name"] = " ".join(name_words).strip()

    if address_words:
        result["address"] = " ".join(address_words).strip()

    if phone_words:
        result["phone_number"] = " ".join(phone_words).strip()

    result["emails"] = email_words
    result["urls"] = url_words

    # If no labeled merchant name found, try to infer from top words
    if not result["name"] and words:
        # Take the first few words as potential merchant name
        top_words = [word.text for word in words[:5] if word.text.strip()]
        if top_words:
            result["name"] = " ".join(top_words).strip()

    return result
