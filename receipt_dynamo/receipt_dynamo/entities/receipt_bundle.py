"""Receipt bundle data structures for efficient listing operations.

This module provides dataclasses for representing receipts with their
associated words and labels in a paginated format, optimized for listing
operations that don't require fetching all related entities.
"""

from dataclasses import dataclass
from typing import Any, Iterator

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


@dataclass
class ReceiptBundle:
    """Bundle of a receipt with its words and labels.

    This is a lighter version of ReceiptDetails used for listing operations
    that don't need to fetch all related entities like lines and letters.
    """

    receipt: Receipt
    words: list[ReceiptWord]
    word_labels: list[ReceiptWordLabel]

    @property
    def key(self) -> str:
        """Get the composite key for this receipt bundle."""
        return f"{self.receipt.image_id}_{self.receipt.receipt_id}"


@dataclass
class ReceiptBundlePage:
    """A page of receipt bundles with pagination support.

    This class represents a paginated result set from listing receipt details.
    Each receipt is uniquely identified by a composite key combining the image
    ID and receipt ID in the format: "{image_id}_{receipt_id}".

    Attributes:
        bundles: A dictionary mapping composite keys to ReceiptBundle
                  objects. The key format is "{image_id}_{receipt_id}" where:
                  - image_id: The UUID of the image containing the receipt
                  - receipt_id: The integer ID of the receipt within that image
                  Example key: "550e8400-e29b-41d4-a716-446655440000_1"

        last_evaluated_key: DynamoDB pagination token for fetching the next
                           page. None if this is the last page of results.

    Example:
        >>> page = client.list_receipt_details(limit=10)
        >>> for key, bundle in page.bundles.items():
        ...     receipt = bundle.receipt
        ...     print(f"Receipt {receipt.receipt_id} from {receipt.image_id}")
        ...     print(f"  Words: {len(bundle.words)}")
        ...     print(f"  Labels: {len(bundle.word_labels)}")
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

    bundles: dict[str, ReceiptBundle]
    last_evaluated_key: dict[str, Any] | None

    # Backwards compatibility aliases
    @property
    def summaries(self) -> dict[str, ReceiptBundle]:
        """Backwards compatibility alias for bundles."""
        return self.bundles

    def __len__(self) -> int:
        """Return the number of bundles in this page."""
        return len(self.bundles)

    def __getitem__(self, key: str) -> ReceiptBundle:
        """Get a bundle by its composite key.

        Args:
            key: The composite key in format "{image_id}_{receipt_id}"

        Returns:
            ReceiptBundle: The receipt bundle for the given key

        Raises:
            KeyError: If the key doesn't exist in this page

        Example:
            >>> bundle = page["550e8400-e29b-41d4-a716-446655440000_1"]
        """
        return self.bundles[key]

    def __contains__(self, key: str) -> bool:
        """Check if a bundle exists by its composite key.

        Args:
            key: The composite key in format "{image_id}_{receipt_id}"

        Returns:
            bool: True if the key exists in this page, False otherwise

        Example:
            >>> if "550e8400-e29b-41d4-a716-446655440000_1" in page:
            ...     bundle = page["550e8400-e29b-41d4-a716-446655440000_1"]
        """
        return key in self.bundles

    def __iter__(self) -> Iterator[str]:
        """Iterate over bundle keys.

        Returns:
            Iterator over the composite keys in this page.

        Example:
            >>> for key in page:
            ...     print(key)
        """
        return iter(self.bundles)

    @property
    def has_more(self) -> bool:
        """Check if there are more pages available.

        Returns:
            bool: True if there are more receipts to fetch, False if this
                  is the last page
        """
        return self.last_evaluated_key is not None

    def keys(self) -> list[str]:
        """Get all composite keys in this page.

        Returns:
            list[str]: List of composite keys in format
                      "{image_id}_{receipt_id}"
        """
        return list(self.bundles.keys())

    def values(self) -> list[ReceiptBundle]:
        """Get all receipt bundles in this page.

        Returns:
            list[ReceiptBundle]: List of all receipt bundles in this page
        """
        return list(self.bundles.values())

    def items(self) -> list[tuple[str, ReceiptBundle]]:
        """Get all key-bundle pairs in this page.

        Returns:
            list[tuple[str, ReceiptBundle]]: List of (key, bundle) tuples
        """
        return list(self.bundles.items())
