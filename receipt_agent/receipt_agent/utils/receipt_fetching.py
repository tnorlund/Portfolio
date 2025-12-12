"""
Utilities for fetching receipt data with fallback methods.

This module provides shared functionality for fetching receipt details
with robust error handling and fallback methods.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient
    from receipt_dynamo.entities.receipt_details import ReceiptDetails

logger = logging.getLogger(__name__)


def fetch_receipt_details_with_fallback(
    dynamo_client: "DynamoClient",
    image_id: str,
    receipt_id: int,
) -> Optional["ReceiptDetails"]:
    """
    Fetch receipt details using primary method, with fallback methods.

    Handles:
    - Trailing characters in image_id (sanitizes)
    - Missing receipt entity (tries direct line/word fetch)
    - Multiple image_id variants

    Args:
        dynamo_client: DynamoDB client
        image_id: Image ID (may have trailing characters like '?')
        receipt_id: Receipt ID

    Returns:
        ReceiptDetails if successful, None otherwise
    """
    try:
        from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
        from receipt_dynamo.entities.receipt_details import ReceiptDetails

        # Sanitize image_id - remove trailing whitespace and special characters
        # Some image_ids may have trailing '?' or other characters
        sanitized_image_id = image_id.rstrip("? \t\n\r")

        # Try sanitized version first, then original if different
        image_ids_to_try = [sanitized_image_id]
        if sanitized_image_id != image_id:
            image_ids_to_try.append(image_id)
            logger.debug(
                f"Sanitized image_id '{image_id}' to '{sanitized_image_id}'"
            )

        # Try to get receipt entity
        receipt = None
        for img_id in image_ids_to_try:
            try:
                receipt = dynamo_client.get_receipt(img_id, receipt_id)
                if receipt:
                    # Use the working image_id for subsequent queries
                    image_id = img_id
                    break
            except EntityNotFoundError:
                continue
            except Exception as e:
                logger.debug(
                    f"Error fetching receipt for {img_id}#{receipt_id}: {e}"
                )
                continue

        # Try to fetch lines and words directly (they might exist even if receipt doesn't)
        lines = []
        words = []

        # Try both sanitized and original image_id for lines/words
        for img_id in image_ids_to_try:
            if lines and words:
                break  # Already found both

            if not lines:
                try:
                    lines = dynamo_client.list_receipt_lines_from_receipt(
                        img_id, receipt_id
                    )
                    if lines:
                        image_id = img_id  # Use working image_id
                        logger.debug(
                            f"Fetched {len(lines)} lines for {img_id}#{receipt_id} via fallback"
                        )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch lines for {img_id}#{receipt_id}: {e}"
                    )

            if not words:
                try:
                    words = dynamo_client.list_receipt_words_from_receipt(
                        img_id, receipt_id
                    )
                    if words:
                        image_id = img_id  # Use working image_id
                        logger.debug(
                            f"Fetched {len(words)} words for {img_id}#{receipt_id} via fallback"
                        )
                except Exception as e:
                    logger.debug(
                        f"Could not fetch words for {img_id}#{receipt_id}: {e}"
                    )

        # If we have lines or words, we can still work with them
        if lines or words:
            if receipt:
                # Full ReceiptDetails with receipt entity
                return ReceiptDetails(
                    receipt=receipt,
                    lines=lines,
                    words=words,
                    letters=[],
                    labels=[],
                )
            else:
                # We have lines/words but no receipt entity
                # Note: Receipt fetch was already attempted in the loop above (lines 56-69),
                # so if it failed there, it will fail here too. Return None and let tools handle it.
                logger.warning(
                    f"Found {len(lines)} lines and {len(words)} words for {image_id}#{receipt_id} "
                    f"but no receipt entity. Tools will work with lines/words only."
                )
                # Return None - tools will handle this case
                return None

        return None

    except Exception as e:
        logger.debug(f"Fallback fetch failed for {image_id}#{receipt_id}: {e}")
        return None
