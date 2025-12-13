"""
Label Harmonizer V3 - Whole Receipt Agent-Based Harmonization
===============================================================

Purpose
-------
Harmonizes all labels for a single receipt using an LLM agent that:
- Processes the entire receipt text as source of truth
- Uses sub-agents for currency detection, totals validation, line item parsing
- Validates financial consistency (grand total, subtotal, tax, line items)
- Uses ChromaDB similarity search for label suggestions
- Provides detailed reasoning and confidence scores

Why V3 is Better Than V2
------------------------
V2 processes labels by merchant and label_type groups across all receipts.
This works well for finding outliers but doesn't validate receipt-level consistency.

V3 processes whole receipts and:
- Validates financial math (line items + tax = grand total)
- Uses receipt text as source of truth (not external APIs)
- Detects currency from receipt text
- Validates all labels together for consistency
- Uses sub-agents to reduce context size

How It Works
------------
1. Load receipt: Get receipt text, words, lines, and all labels
2. Run agent: Agent reasons about the receipt and validates labels
3. Apply fixes: Update labels in DynamoDB (if not dry_run)

Usage
-----
```python
from receipt_agent.agents.label_harmonizer.tools.label_harmonizer_v3 import (
    LabelHarmonizerV3,
)

harmonizer = LabelHarmonizerV3(dynamo_client, chroma_client, embed_fn)
result = await harmonizer.harmonize_receipt(image_id, receipt_id)
print(f"Updated {result.total_updated} labels")

# Process multiple receipts
results = await harmonizer.harmonize_receipts(receipt_keys)
```
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langchain_ollama import ChatOllama

from receipt_agent.config.settings import Settings, get_settings

try:
    import langsmith as ls
    from langsmith import traceable

    HAS_LANGSMITH = True
except ImportError:
    ls = None  # type: ignore
    HAS_LANGSMITH = False

    def traceable(func):
        return func


try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    CORE_LABELS = {
        "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
        "STORE_HOURS": "Printed business hours or opening times for the merchant.",
        "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
        "WEBSITE": "Web or email address printed on the receipt.",
        "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
        "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
        "DATE": "Calendar date of the transaction.",
        "TIME": "Time of the transaction.",
        "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
        "COUPON": "Coupon code or description that reduces price.",
        "DISCOUNT": "Any non-coupon discount line item.",
        "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
        "QUANTITY": "Numeric count or weight of the item.",
        "UNIT_PRICE": "Price per single unit / weight before tax.",
        "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
        "SUBTOTAL": "Sum of all line totals before tax and discounts.",
        "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
        "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    }

logger = logging.getLogger(__name__)


@dataclass
class ReceiptLabelResult:
    """Result of harmonization for a single receipt."""

    image_id: str
    receipt_id: int
    total_labels: int = 0
    labels_updated: int = 0
    labels_skipped: int = 0
    labels_failed: int = 0
    currency_detected: Optional[str] = None
    totals_valid: bool = False
    totals_issues: List[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    updates: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    """Result of applying fixes to DynamoDB."""

    total_processed: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    total_needs_review: int = 0
    errors: List[str] = field(default_factory=list)


class LabelHarmonizerV3:
    """
    Agent-based harmonizer for whole receipt label validation.

    Uses an LLM agent to reason about a receipt and validate all labels
    together, ensuring financial consistency and correctness.

    Key Features:
    - Whole receipt processing (not per-label-type)
    - Receipt text as source of truth
    - Financial validation (totals, line items, currency)
    - Sub-agents for context reduction
    - ChromaDB similarity search for suggestions
    - Detailed reasoning and confidence scores
    - Dry-run mode for safety

    Example:
        ```python
        harmonizer = LabelHarmonizerV3(dynamo_client, chroma_client, embed_fn)

        # Harmonize a single receipt
        result = await harmonizer.harmonize_receipt(image_id, receipt_id)
        print(f"Updated {result.labels_updated} labels")

        # Process multiple receipts
        receipt_keys = [(image_id1, receipt_id1), (image_id2, receipt_id2)]
        results = await harmonizer.harmonize_receipts(receipt_keys)
        ```
    """

    def __init__(
        self,
        dynamo_client: Any,
        chroma_client: Optional[Any] = None,
        embed_fn: Optional[Any] = None,
        llm: Optional[Any] = None,
        settings: Optional[Settings] = None,
    ):
        """
        Initialize the harmonizer.

        Args:
            dynamo_client: DynamoDB client with receipt query methods
            chroma_client: ChromaDB client for similarity search
            embed_fn: Function to generate embeddings
            llm: Optional LLM client for agent reasoning
            settings: Optional settings
        """
        self.dynamo = dynamo_client
        self.chroma = chroma_client
        self.embed_fn = embed_fn

        # Store settings for later use
        if settings is None:
            settings = get_settings()
        self.settings = settings

        if llm is None:
            api_key = settings.ollama_api_key.get_secret_value()
            self.llm = ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                client_kwargs={
                    "headers": (
                        {"Authorization": f"Bearer {api_key}"}
                        if api_key
                        else {}
                    ),
                    "timeout": 120,
                },
                temperature=0.3,  # Slightly higher for reasoning
            )
        else:
            self.llm = llm

        self._agent_graph: Optional[Any] = None
        self._agent_state_holder: Optional[dict] = None
        self._last_results: List[ReceiptLabelResult] = []

    async def harmonize_receipt(
        self,
        image_id: str,
        receipt_id: int,
        dry_run: bool = True,
    ) -> ReceiptLabelResult:
        """
        Harmonize labels for a single receipt.

        Args:
            image_id: Image ID containing the receipt
            receipt_id: Receipt ID within the image
            dry_run: If True, only report what would be updated

        Returns:
            ReceiptLabelResult with harmonization results
        """
        # Initialize agent if needed
        if self._agent_graph is None:
            from receipt_agent.agents.label_harmonizer import (
                create_label_harmonizer_graph,
            )

            self._agent_graph, self._agent_state_holder = (
                create_label_harmonizer_graph(
                    dynamo_client=self.dynamo,
                    chroma_client=self.chroma,
                    embed_fn=self.embed_fn,
                    settings=self.settings,
                )
            )

        # Run agent
        from receipt_agent.agents.label_harmonizer import (
            run_label_harmonizer_agent,
        )

        result = await run_label_harmonizer_agent(
            graph=self._agent_graph,
            state_holder=self._agent_state_holder,
            image_id=image_id,
            receipt_id=receipt_id,
            dry_run=dry_run,
        )

        return result

    async def harmonize_receipts(
        self,
        receipt_keys: List[Tuple[str, int]],
        dry_run: bool = True,
        limit: Optional[int] = None,
    ) -> List[ReceiptLabelResult]:
        """
        Harmonize labels for multiple receipts.

        Args:
            receipt_keys: List of (image_id, receipt_id) tuples
            dry_run: If True, only report what would be updated
            limit: Maximum number of receipts to process (for testing)

        Returns:
            List of ReceiptLabelResult objects
        """
        if limit:
            receipt_keys = receipt_keys[:limit]

        results = []
        for i, (image_id, receipt_id) in enumerate(receipt_keys):
            if (i + 1) % 10 == 0:
                logger.info(
                    f"Processed {i + 1}/{len(receipt_keys)} receipts..."
                )

            try:
                result = await self.harmonize_receipt(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    dry_run=dry_run,
                )
                results.append(result)
            except Exception as e:
                logger.exception(
                    f"Failed to harmonize {image_id}#{receipt_id}: {e}"
                )
                results.append(
                    ReceiptLabelResult(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        errors=[str(e)],
                    )
                )

        self._last_results = results
        return results

    async def apply_fixes(
        self,
        results: Optional[List[ReceiptLabelResult]] = None,
        dry_run: bool = True,
        min_confidence: float = 0.7,
    ) -> UpdateResult:
        """
        Apply harmonization fixes to DynamoDB.

        Args:
            results: List of ReceiptLabelResult objects (uses last results if None)
            dry_run: If True, only report what would be updated
            min_confidence: Minimum confidence (0.0 to 1.0) to apply fix

        Returns:
            UpdateResult with counts and errors
        """
        if results is None:
            results = self._last_results

        if not results:
            logger.warning("No results to apply fixes for")
            return UpdateResult()

        update_result = UpdateResult()

        for receipt_result in results:
            if receipt_result.errors:
                update_result.total_failed += 1
                update_result.errors.extend(receipt_result.errors)
                continue

            if receipt_result.confidence < min_confidence:
                logger.debug(
                    f"Skipping {receipt_result.image_id}#{receipt_result.receipt_id}: "
                    f"confidence {receipt_result.confidence} < {min_confidence}"
                )
                update_result.total_skipped += 1
                continue

            if dry_run:
                update_result.total_updated += receipt_result.labels_updated
                update_result.total_processed += 1
                continue

            # Apply updates with safeguards for add vs update
            for update in receipt_result.updates:
                try:
                    from datetime import datetime

                    from receipt_dynamo.constants import ValidationStatus
                    from receipt_dynamo.data.shared_exceptions import (
                        EntityNotFoundError,
                    )
                    from receipt_dynamo.entities import ReceiptWordLabel

                    image_id = update["image_id"]
                    receipt_id = update["receipt_id"]
                    line_id = update["line_id"]
                    word_id = update["word_id"]
                    new_label_type = update["label"]
                    new_validation_status = update.get(
                        "validation_status", ValidationStatus.VALID.value
                    )
                    reasoning = update.get(
                        "reasoning",
                        f"Label harmonized by LabelHarmonizerV3: {receipt_result.reasoning}",
                    )

                    # Check if label with this type already exists
                    existing_label = None
                    try:
                        existing_label = self.dynamo.get_receipt_word_label(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=new_label_type,
                        )
                    except EntityNotFoundError:
                        # Label doesn't exist - will add new one
                        pass

                    # Get all existing labels for this word (audit trail)
                    all_existing_labels, _ = (
                        self.dynamo.list_receipt_word_labels_for_word(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                        )
                    )

                    # Find current label (if different from new one)
                    current_label = None
                    for label in all_existing_labels:
                        # Current label is one that's VALID or most recent
                        if (
                            label.validation_status
                            == ValidationStatus.VALID.value
                            or current_label is None
                        ):
                            current_label = label

                    # Determine if we should add or update
                    if existing_label:
                        # Same label type exists - UPDATE it
                        existing_label.validation_status = (
                            new_validation_status
                        )
                        existing_label.reasoning = (
                            reasoning
                            if reasoning
                            else existing_label.reasoning
                        )
                        # Update timestamp to reflect this change
                        existing_label.timestamp_added = (
                            datetime.utcnow().isoformat()
                        )

                        self.dynamo.update_receipt_word_label(existing_label)
                        update_result.total_updated += 1
                        logger.debug(
                            f"Updated existing label {new_label_type} for "
                            f"{image_id[:8]}...#{receipt_id}#{line_id}#{word_id}"
                        )

                    elif (
                        current_label and current_label.label != new_label_type
                    ):
                        # Different label type - ADD new label, preserve old one
                        # Mark old label as consolidated if it was VALID
                        if (
                            current_label.validation_status
                            == ValidationStatus.VALID.value
                        ):
                            # Update old label to mark it as superseded
                            current_label.validation_status = (
                                ValidationStatus.INVALID.value
                            )
                            self.dynamo.update_receipt_word_label(
                                current_label
                            )

                        # Create new label with consolidation info
                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=new_label_type,
                            reasoning=reasoning,
                            timestamp_added=datetime.utcnow().isoformat(),
                            validation_status=new_validation_status,
                            label_proposed_by="label-harmonizer-v3",
                            label_consolidated_from=(
                                current_label.label if current_label else None
                            ),
                        )

                        self.dynamo.add_receipt_word_label(new_label)
                        update_result.total_updated += 1
                        logger.debug(
                            f"Added new label {new_label_type} (consolidated from {current_label.label if current_label else 'none'}) "
                            f"for {image_id[:8]}...#{receipt_id}#{line_id}#{word_id}"
                        )

                    else:
                        # No existing label - ADD new one
                        new_label = ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=new_label_type,
                            reasoning=reasoning,
                            timestamp_added=datetime.utcnow().isoformat(),
                            validation_status=new_validation_status,
                            label_proposed_by="label-harmonizer-v3",
                        )

                        self.dynamo.add_receipt_word_label(new_label)
                        update_result.total_updated += 1
                        logger.debug(
                            f"Added new label {new_label_type} for "
                            f"{image_id[:8]}...#{receipt_id}#{line_id}#{word_id}"
                        )

                except Exception as e:
                    logger.exception(f"Failed to apply label update: {e}")
                    update_result.total_failed += 1
                    update_result.errors.append(
                        f"{update.get('image_id', 'unknown')}#{update.get('receipt_id', 'unknown')}#{update.get('line_id', 'unknown')}#{update.get('word_id', 'unknown')}: {str(e)}"
                    )

            update_result.total_processed += 1

        logger.info(
            f"Update complete: {update_result.total_updated} updated, "
            f"{update_result.total_skipped} skipped, {update_result.total_failed} failed"
        )

        return update_result

    def print_summary(
        self, results: Optional[List[ReceiptLabelResult]] = None
    ) -> None:
        """Print a human-readable summary of harmonization results."""
        if results is None:
            results = self._last_results

        if not results:
            print("No results available.")
            return

        total_receipts = len(results)
        total_labels = sum(r.total_labels for r in results)
        total_updated = sum(r.labels_updated for r in results)
        total_failed = sum(1 for r in results if r.errors)
        valid_totals = sum(1 for r in results if r.totals_valid)

        print("=" * 70)
        print("LABEL HARMONIZER V3 REPORT (Whole Receipt)")
        print("=" * 70)
        print(f"Total receipts processed: {total_receipts}")
        print(f"Total labels: {total_labels}")
        print(f"Labels updated: {total_updated}")
        print(f"Receipts with errors: {total_failed}")
        print(f"Receipts with valid totals: {valid_totals}")
        print()

        # Show some examples
        successful = [
            r for r in results if r.labels_updated > 0 and not r.errors
        ]
        if successful:
            print("Sample harmonization results:")
            for r in successful[:5]:
                print(
                    f"  {r.image_id[:8]}...#{r.receipt_id}: "
                    f"{r.labels_updated} labels updated, "
                    f"currency={r.currency_detected}, "
                    f"totals_valid={r.totals_valid}, "
                    f"confidence={r.confidence:.0%}"
                )
            if len(successful) > 5:
                print(f"  ... and {len(successful) - 5} more")
            print()

        # Show errors
        errors = [r for r in results if r.errors]
        if errors:
            print(f"Errors ({len(errors)}):")
            for r in errors[:3]:
                print(
                    f"  ⛔ {r.image_id[:8]}...#{r.receipt_id}: {r.errors[0][:50]}"
                )
            if len(errors) > 3:
                print(f"  ... and {len(errors) - 3} more")
