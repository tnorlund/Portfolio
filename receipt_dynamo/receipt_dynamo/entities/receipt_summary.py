"""Receipt summary data structures for efficient listing operations.

This module provides dataclasses for representing receipts with their
associated words and labels in a paginated format, optimized for listing
operations that don't require fetching all related entities.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


@dataclass
class ReceiptSummary:
    """Summary of a receipt with its words and labels.

    This is a lighter version of ReceiptDetails used for listing operations
    that don't need to fetch all related entities like lines and letters.
    """

    receipt: Receipt
    words: List[ReceiptWord]
    word_labels: List[ReceiptWordLabel]

    @property
    def key(self) -> str:
        """Get the composite key for this receipt summary."""
        return f"{self.receipt.image_id}_{self.receipt.receipt_id}"


@dataclass
class ReceiptSummaryPage:
    """A page of receipt summaries with pagination support.

    This class represents a paginated result set from listing receipt details.
    Each receipt is uniquely identified by a composite key combining the image
    ID and receipt ID in the format: "{image_id}_{receipt_id}".

    Attributes:
        summaries: A dictionary mapping composite keys to ReceiptSummary
                  objects. The key format is "{image_id}_{receipt_id}" where:
                  - image_id: The UUID of the image containing the receipt
                  - receipt_id: The integer ID of the receipt within that image
                  Example key: "550e8400-e29b-41d4-a716-446655440000_1"

        last_evaluated_key: DynamoDB pagination token for fetching the next
                           page. None if this is the last page of results.

    Example:
        >>> page = client.list_receipt_details(limit=10)
        >>> for key, summary in page.summaries.items():
        ...     receipt = summary.receipt
        ...     print(f"Receipt {receipt.receipt_id} from {receipt.image_id}")
        ...     print(f"  Words: {len(summary.words)}")
        ...     print(f"  Labels: {len(summary.word_labels)}")
        >>>
        >>> # Check if there are more pages
        >>> if page.has_more:
        ...     next_page = client.list_receipt_details(
        ...         limit=10,
        ...         last_evaluated_key=page.last_evaluated_key
        ...     )

    Note:
        The composite key format ensures global uniqueness since:
        - Each image has a unique UUID
        - Receipt IDs are unique within an image
        - The combination "{image_id}_{receipt_id}" is globally unique
    """

    summaries: Dict[str, ReceiptSummary]
    last_evaluated_key: Optional[Dict[str, Any]]

    def __len__(self) -> int:
        """Return the number of summaries in this page."""
        return len(self.summaries)

    def __getitem__(self, key: str) -> ReceiptSummary:
        """Get a summary by its composite key.

        Args:
            key: The composite key in format "{image_id}_{receipt_id}"

        Returns:
            ReceiptSummary: The receipt summary for the given key

        Raises:
            KeyError: If the key doesn't exist in this page

        Example:
            >>> summary = page["550e8400-e29b-41d4-a716-446655440000_1"]
        """
        return self.summaries[key]

    def __contains__(self, key: str) -> bool:
        """Check if a summary exists by its composite key.

        Args:
            key: The composite key in format "{image_id}_{receipt_id}"

        Returns:
            bool: True if the key exists in this page, False otherwise

        Example:
            >>> if "550e8400-e29b-41d4-a716-446655440000_1" in page:
            ...     summary = page["550e8400-e29b-41d4-a716-446655440000_1"]
        """
        return key in self.summaries

    @property
    def has_more(self) -> bool:
        """Check if there are more pages available.

        Returns:
            bool: True if there are more receipts to fetch, False if this
                  is the last page
        """
        return self.last_evaluated_key is not None

    def keys(self) -> List[str]:
        """Get all composite keys in this page.

        Returns:
            List[str]: List of composite keys in format
                      "{image_id}_{receipt_id}"
        """
        return list(self.summaries.keys())

    def values(self) -> List[ReceiptSummary]:
        """Get all receipt summaries in this page.

        Returns:
            List[ReceiptSummary]: List of all receipt summaries in this page
        """
        return list(self.summaries.values())

    def items(self) -> List[tuple[str, ReceiptSummary]]:
        """Get all key-summary pairs in this page.

        Returns:
            List[tuple[str, ReceiptSummary]]: List of (key, summary) tuples
        """
        return list(self.summaries.items())
