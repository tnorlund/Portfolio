"""Utility functions for LangGraph nodes."""

from typing import Dict, List, Optional


def get_dynamo_client():
    """Get DynamoDB client instance.
    
    In production, this would return the actual client.
    For now, returns None for mocking.
    """
    # from receipt_dynamo import DynamoClient
    # return DynamoClient()
    return None


def get_pinecone_client():
    """Get Pinecone client instance.
    
    In production, this would return the actual client.
    For now, returns None for mocking.
    """
    # import pinecone
    # return pinecone.Index("receipt-embeddings")
    return None


def format_receipt_words_for_prompt(
    receipt_words: List[Dict],
    max_words: int = 50
) -> str:
    """Format receipt words for inclusion in prompts.
    
    Args:
        receipt_words: List of word dictionaries
        max_words: Maximum number of words to include
        
    Returns:
        Formatted string for prompt
    """
    lines = []
    for word in receipt_words[:max_words]:
        lines.append(f"{word['word_id']}|{word['text']}")
    
    if len(receipt_words) > max_words:
        lines.append(f"... ({len(receipt_words) - max_words} more words)")
    
    return "\n".join(lines)


def get_word_text(receipt_words: List[Dict], word_id: int) -> Optional[str]:
    """Get text for a specific word ID.
    
    Args:
        receipt_words: List of word dictionaries
        word_id: ID of word to find
        
    Returns:
        Word text or None if not found
    """
    for word in receipt_words:
        if word.get("word_id") == word_id:
            return word.get("text")
    return None


def get_word_by_id(receipt_words: List[Dict], word_id: int) -> Optional[Dict]:
    """Get full word dictionary by ID.
    
    Args:
        receipt_words: List of word dictionaries  
        word_id: ID of word to find
        
    Returns:
        Word dictionary or None if not found
    """
    for word in receipt_words:
        if word.get("word_id") == word_id:
            return word
    return None


def filter_meaningful_words(receipt_words: List[Dict]) -> List[Dict]:
    """Filter out noise words (punctuation, separators, etc).
    
    Args:
        receipt_words: List of word dictionaries
        
    Returns:
        List of meaningful words only
    """
    noise_patterns = {
        # Single punctuation
        ".", ",", ";", ":", "!", "?", "'", '"', "-", "_",
        # Separators
        "|", "/", "\\", "~", "=", "+", "*", "&", "%",
        # Common OCR artifacts
        "•", "·", "…", "—",
    }
    
    meaningful = []
    for word in receipt_words:
        text = word.get("text", "").strip()
        if text and text not in noise_patterns and len(text) > 1:
            meaningful.append(word)
    
    return meaningful