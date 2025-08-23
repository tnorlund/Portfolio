"""
ID utilities for converting receipt entities to vector store identifiers.

This module provides standardized functions for converting ReceiptWord and 
ReceiptLine entities to their corresponding ChromaDB/vector store IDs.

The ID format follows the established pattern used throughout the embedding pipeline:
- Words: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
- Lines: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}

These functions ensure consistent ID generation across all vector store operations.
"""

from receipt_dynamo.entities import ReceiptWord, ReceiptLine


def word_to_vector_id(word: ReceiptWord) -> str:
    """
    Convert a ReceiptWord entity to its vector store ID.

    This function creates a standardized identifier that uniquely identifies
    a word across all receipts in the vector store. The format matches the
    pattern used in the embedding pipeline.

    Format:
        IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}

    Args:
        word: ReceiptWord entity containing the word's position and metadata

    Returns:
        Formatted vector store ID string

    Example:
        >>> from receipt_dynamo.entities import ReceiptWord
        >>> word = ReceiptWord(
        ...     image_id="abc123def456",
        ...     receipt_id=42,
        ...     line_id=3,
        ...     word_id=7,
        ...     text="$12.99",
        ...     x1=100, y1=200, x2=150, y2=220
        ... )
        >>> word_to_vector_id(word)
        'IMAGE#abc123def456#RECEIPT#00042#LINE#00003#WORD#00007'
    """
    return (
        f"IMAGE#{word.image_id}#RECEIPT#{word.receipt_id:05d}#LINE#"
        f"{word.line_id:05d}#WORD#{word.word_id:05d}"
    )


def line_to_vector_id(line: ReceiptLine) -> str:
    """
    Convert a ReceiptLine entity to its vector store ID.

    This function creates a standardized identifier that uniquely identifies
    a line across all receipts in the vector store. The format matches the
    pattern used in the embedding pipeline.

    Format:
        IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}

    Args:
        line: ReceiptLine entity containing the line's position and metadata

    Returns:
        Formatted vector store ID string

    Example:
        >>> from receipt_dynamo.entities import ReceiptLine
        >>> line = ReceiptLine(
        ...     image_id="abc123def456",
        ...     receipt_id=42,
        ...     line_id=3,
        ...     text="Total: $12.99",
        ...     x1=100, y1=200, x2=250, y2=220
        ... )
        >>> line_to_vector_id(line)
        'IMAGE#abc123def456#RECEIPT#00042#LINE#00003'
    """
    return (
        f"IMAGE#{line.image_id}#RECEIPT#{line.receipt_id:05d}#LINE#"
        f"{line.line_id:05d}"
    )


def parse_word_vector_id(vector_id: str) -> dict:
    """
    Parse a word vector ID back into its component parts.

    This function is the inverse of word_to_vector_id(), extracting the
    individual components from a formatted vector ID string.

    Args:
        vector_id: Formatted vector ID string

    Returns:
        Dictionary with keys: image_id, receipt_id, line_id, word_id

    Raises:
        ValueError: If the vector_id format is invalid

    Example:
        >>> parse_word_vector_id("IMAGE#abc123#RECEIPT#00042#LINE#00003#WORD#00007")
        {
            'image_id': 'abc123',
            'receipt_id': 42,
            'line_id': 3,
            'word_id': 7
        }
    """
    parts = vector_id.split("#")
    
    if len(parts) != 8:
        raise ValueError(
            f"Invalid word vector ID format: {vector_id}. "
            f"Expected format: IMAGE#<id>#RECEIPT#<id>#LINE#<id>#WORD#<id>, "
            f"but got {len(parts)} parts"
        )
    
    if parts[0] != "IMAGE" or parts[2] != "RECEIPT" or parts[4] != "LINE" or parts[6] != "WORD":
        raise ValueError(
            f"Invalid word vector ID format: {vector_id}. "
            f"Expected prefixes: IMAGE, RECEIPT, LINE, WORD"
        )
    
    try:
        return {
            'image_id': parts[1],
            'receipt_id': int(parts[3]),
            'line_id': int(parts[5]),
            'word_id': int(parts[7])
        }
    except ValueError as e:
        raise ValueError(f"Invalid numeric values in vector ID {vector_id}: {e}")


def parse_line_vector_id(vector_id: str) -> dict:
    """
    Parse a line vector ID back into its component parts.

    This function is the inverse of line_to_vector_id(), extracting the
    individual components from a formatted vector ID string.

    Args:
        vector_id: Formatted vector ID string

    Returns:
        Dictionary with keys: image_id, receipt_id, line_id

    Raises:
        ValueError: If the vector_id format is invalid

    Example:
        >>> parse_line_vector_id("IMAGE#abc123#RECEIPT#00042#LINE#00003")
        {
            'image_id': 'abc123',
            'receipt_id': 42,
            'line_id': 3
        }
    """
    parts = vector_id.split("#")
    
    if len(parts) != 6:
        raise ValueError(
            f"Invalid line vector ID format: {vector_id}. "
            f"Expected format: IMAGE#<id>#RECEIPT#<id>#LINE#<id>, "
            f"but got {len(parts)} parts"
        )
    
    if parts[0] != "IMAGE" or parts[2] != "RECEIPT" or parts[4] != "LINE":
        raise ValueError(
            f"Invalid line vector ID format: {vector_id}. "
            f"Expected prefixes: IMAGE, RECEIPT, LINE"
        )
    
    try:
        return {
            'image_id': parts[1],
            'receipt_id': int(parts[3]),
            'line_id': int(parts[5])
        }
    except ValueError as e:
        raise ValueError(f"Invalid numeric values in vector ID {vector_id}: {e}")


def is_word_vector_id(vector_id: str) -> bool:
    """
    Check if a vector ID represents a word.

    Args:
        vector_id: Vector ID string to check

    Returns:
        True if the ID represents a word, False otherwise

    Example:
        >>> is_word_vector_id("IMAGE#abc#RECEIPT#00042#LINE#00003#WORD#00007")
        True
        >>> is_word_vector_id("IMAGE#abc#RECEIPT#00042#LINE#00003")
        False
    """
    try:
        parts = vector_id.split("#")
        return len(parts) == 8 and parts[6] == "WORD"
    except (AttributeError, IndexError):
        return False


def is_line_vector_id(vector_id: str) -> bool:
    """
    Check if a vector ID represents a line.

    Args:
        vector_id: Vector ID string to check

    Returns:
        True if the ID represents a line, False otherwise

    Example:
        >>> is_line_vector_id("IMAGE#abc#RECEIPT#00042#LINE#00003")
        True
        >>> is_line_vector_id("IMAGE#abc#RECEIPT#00042#LINE#00003#WORD#00007")
        False
    """
    try:
        parts = vector_id.split("#")
        return len(parts) == 6 and parts[4] == "LINE"
    except (AttributeError, IndexError):
        return False