"""
Label Harmonizer - Merchant-Based Label Consistency
===================================================

Purpose
-------
Ensures label consistency across receipts from the same merchant by grouping
labels by merchant_name and label type, then finding consensus using ChromaDB
similarity search.

Key Insight
-----------
Receipts from the same merchant should have consistent labels for similar words.
Any inconsistency indicates a data quality issue:
- OCR errors
- Mislabeling
- Missing labels

How It Works
------------
**Works PER LABEL TYPE across ALL RECEIPTS** (not per receipt):
1. **Load labels by type**: Use GSI1 to load ALL labels for a specific CORE_LABEL type
   (e.g., all GRAND_TOTAL labels across all receipts)
2. **Group by merchant**: Group labels by merchant_name (from ReceiptMetadata)
   (e.g., all GRAND_TOTAL labels from "Vons" receipts)
3. **Find similar words**: Use ChromaDB to find semantically similar words within merchant group
4. **Compute consensus**: Find most common VALID label for similar words
5. **Identify outliers**: Flag labels that differ from consensus
6. **Apply fixes**: Update labels in DynamoDB (preserving originals)

Data Model
----------
- **ReceiptWordLabel**: Contains label metadata (label, validation_status, reasoning)
  - References word via: image_id, receipt_id, line_id, word_id
  - Does NOT contain word text
- **ReceiptWord**: Separate entity containing word text, bounding_box, confidence
  - Must be fetched separately using the same keys
- **ReceiptLine**: Separate entity containing line text
  - Not currently used by harmonizer but available if needed

What Gets Updated
-----------------
The harmonizer updates labels to ensure consistency:
- `label` ← Consensus label (if different)
- `validation_status` ← VALID (if high confidence)

All similar words from the same merchant will have consistent labels after harmonization.

Usage
-----
```python
from receipt_agent.tools.label_harmonizer import LabelHarmonizer

harmonizer = LabelHarmonizer(dynamo_client, chroma_client, embed_fn)
report = await harmonizer.harmonize_label_type("GRAND_TOTAL")
harmonizer.print_summary(report)

# Apply fixes (dry run first)
result = await harmonizer.apply_fixes(dry_run=True)
print(f"Would update {result.total_updated} labels")

# Actually apply fixes
result = await harmonizer.apply_fixes(dry_run=False)
print(f"Updated {result.total_updated} labels")
```
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List, Tuple
from collections import defaultdict

from langchain_ollama import ChatOllama
from receipt_agent.config.settings import Settings, get_settings

try:
    import langsmith as ls
    from langsmith import traceable
except ImportError:
    # LangSmith not available - traceable will be a no-op
    ls = None
    def traceable(func):
        return func

try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available - include all CORE_LABELS definitions
    CORE_LABELS = {
        # ── Merchant & store info ───────────────────────────────────
        "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
        "STORE_HOURS": "Printed business hours or opening times for the merchant.",
        "PHONE_NUMBER": "Telephone number printed on the receipt "
        "(store's main line).",
        "WEBSITE": "Web or email address printed on the receipt "
        "(e.g., sprouts.com).",
        "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
        # ── Location / address ──────────────────────────────────────
        "ADDRESS_LINE": "Full address line (street + city etc.) printed on "
        "the receipt.",
        # ── Transaction info ───────────────────────────────────────
        "DATE": "Calendar date of the transaction.",
        "TIME": "Time of the transaction.",
        "PAYMENT_METHOD": "Payment instrument summary "
        "(e.g., VISA ••••1234, CASH).",
        "COUPON": "Coupon code or description that reduces price.",
        "DISCOUNT": "Any non-coupon discount line item "
        "(e.g., 10% member discount).",
        # ── Line-item fields ───────────────────────────────────────
        "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
        "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
        "UNIT_PRICE": "Price per single unit / weight before tax.",
        "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
        # ── Totals & taxes ──────────────────────────────────────────
        "SUBTOTAL": "Sum of all line totals before tax and discounts.",
        "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
        "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    }

logger = logging.getLogger(__name__)


def _build_chromadb_word_id(image_id: str, receipt_id: int, line_id: int, word_id: int) -> str:
    """Build ChromaDB document ID for a word."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


@dataclass
class LabelRecord:
    """
    A single label record.

    Attributes:
        image_id: UUID of the image containing this receipt
        receipt_id: Receipt number within the image
        line_id: Line number containing the word
        word_id: Word number within the line
        label: Current label value
        validation_status: Current validation status
        merchant_name: Merchant name (from ReceiptMetadata)
        word_text: Text of the word (for reference)
    """
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    validation_status: Optional[str] = None
    merchant_name: Optional[str] = None
    word_text: Optional[str] = None


@dataclass
class MerchantLabelGroup:
    """
    A group of labels sharing the same merchant_name and label type.

    Labels from the same merchant should be consistent for similar words.
    Any differences indicate data quality issues.

    Attributes:
        merchant_name: The merchant name shared by all labels in this group
        label_type: The CORE_LABEL type (e.g., "GRAND_TOTAL")
        labels: List of labels in this group
        consensus_label: Most common VALID label
        similarity_clusters: Groups of similar words (using ChromaDB)
        outliers: Words that don't belong in this group (conflicts)
    """
    merchant_name: str
    label_type: str
    labels: list[LabelRecord] = field(default_factory=list)
    consensus_label: Optional[str] = None
    similarity_clusters: list[list[LabelRecord]] = field(default_factory=list)
    outliers: list[LabelRecord] = field(default_factory=list)
    outlier_run_trees: dict[str, Any] = field(default_factory=dict)  # word_id -> run_tree for child trace linking


@dataclass
class HarmonizerResult:
    """
    Result of harmonization for a single label.

    Attributes:
        image_id, receipt_id, line_id, word_id: Label identifier
        merchant_name: Merchant name
        label_type: CORE_LABEL type
        current_label: Current label value
        consensus_label: Recommended label value
        suggested_label_type: If outlier, suggested correct CORE_LABEL type (e.g., PRODUCT_NAME)
        needs_update: Whether this label needs updating
        changes_needed: List of changes to apply (human-readable)
        confidence: How confident we are in the recommendation (0-100)
        group_size: Number of similar labels (larger = higher confidence)
        cluster_size: Number of labels in similarity cluster
    """
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    merchant_name: Optional[str]
    label_type: str
    current_label: str
    consensus_label: Optional[str]
    suggested_label_type: Optional[str] = None  # For outliers: what CORE_LABEL type should this be?
    needs_update: bool = False
    changes_needed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    group_size: int = 1
    cluster_size: int = 1


@dataclass
class UpdateResult:
    """
    Result of applying fixes to DynamoDB.

    Attributes:
        total_processed: Number of labels processed
        total_updated: Number of labels actually updated
        total_skipped: Number of labels skipped (already consistent)
        total_failed: Number of labels that failed to update
        errors: List of error messages for failed updates
    """
    total_processed: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    errors: list[str] = field(default_factory=list)


class LabelHarmonizer:
    """
    Harmonizes receipt word labels by grouping by merchant and finding consensus.

    This class identifies inconsistencies in receipt word labels and can apply
    fixes by updating labels in DynamoDB.

    Key Features:
    - Groups labels by merchant_name + label_type
    - Uses ChromaDB to find similar words
    - Computes consensus from VALID labels
    - Applies fixes to labels (preserving originals)

    Example:
        ```python
        harmonizer = LabelHarmonizer(dynamo_client, chroma_client, embed_fn)

        # Analyze labels for a specific type
        report = await harmonizer.harmonize_label_type("GRAND_TOTAL")
        harmonizer.print_summary(report)

        # Apply fixes (dry run first)
        result = await harmonizer.apply_fixes(dry_run=True)
        print(f"Would update {result.total_updated} labels")

        # Actually apply fixes
        result = await harmonizer.apply_fixes(dry_run=False)
        print(f"Updated {result.total_updated} labels")
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
            dynamo_client: DynamoDB client with label query methods
            chroma_client: ChromaDB client for similarity search
            embed_fn: Function to generate embeddings (optional, for similarity search)
            llm: Optional LLM client for outlier detection (if None, will create one)
            settings: Optional settings (used if llm is None)
        """
        self.dynamo = dynamo_client
        self.chroma = chroma_client
        self.embed_fn = embed_fn

        # Initialize LLM if not provided
        # Note: We don't set format here - it will be set per-call with the Pydantic schema
        # This follows Ollama best practices for structured output
        if llm is None:
            if settings is None:
                settings = get_settings()
            api_key = settings.ollama_api_key.get_secret_value()
            self.llm = ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                client_kwargs={
                    "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
                    "timeout": 120,
                },
                temperature=0.0,
            )
        else:
            self.llm = llm

        self._merchant_groups: dict[Tuple[str, str], MerchantLabelGroup] = {}
        self._no_merchant_labels: list[LabelRecord] = []
        self._last_report: Optional[dict[str, Any]] = None
        self._label_type: Optional[str] = None

    def _stream_labels_by_type(self, label_type: str):
        """
        Stream labels for a specific label type from DynamoDB (generator).

        **Memory-efficient**: Yields labels in batches instead of loading all into memory.
        Uses GSI1 to efficiently query by label type.

        Args:
            label_type: CORE_LABEL type (e.g., "GRAND_TOTAL")

        Yields:
            Batches of ReceiptWordLabel objects
        """
        last_key = None
        while True:
            batch, last_key = self.dynamo.get_receipt_word_labels_by_label(
                label=label_type,
                limit=1000,
                last_evaluated_key=last_key,
            )
            if not batch:
                break
            yield batch
            if not last_key:
                break

    def _get_merchant_for_labels(self, labels: list) -> dict:
        """
        Get merchant_name for a batch of labels.

        Fetches ReceiptMetadata for unique receipt keys in the batch.

        Args:
            labels: List of ReceiptWordLabel objects

        Returns:
            Dict mapping (image_id, receipt_id) -> merchant_name
        """
        receipt_keys = set((l.image_id, l.receipt_id) for l in labels)
        metadata_by_key = {}

        for image_id, receipt_id in receipt_keys:
            try:
                metadata = self.dynamo.get_receipt_metadata(image_id, receipt_id)
                if metadata and metadata.merchant_name:
                    metadata_by_key[(image_id, receipt_id)] = metadata.merchant_name
            except Exception as e:
                logger.debug(f"Could not get metadata for {image_id}#{receipt_id}: {e}")

        return metadata_by_key

    def _get_words_for_labels(self, labels: list) -> dict:
        """
        Get word text for a batch of labels.

        Fetches ReceiptWord entities for unique word keys in the batch.

        Args:
            labels: List of ReceiptWordLabel objects

        Returns:
            Dict mapping (image_id, receipt_id, line_id, word_id) -> word_text
        """
        word_keys = set((l.image_id, l.receipt_id, l.line_id, l.word_id) for l in labels)
        words_by_key = {}
        words_found = 0
        words_missing = 0

        for image_id, receipt_id, line_id, word_id in word_keys:
            try:
                # Note: get_receipt_word takes (receipt_id, image_id, line_id, word_id)
                word = self.dynamo.get_receipt_word(receipt_id, image_id, line_id, word_id)
                if word:
                    words_by_key[(image_id, receipt_id, line_id, word_id)] = word.text
                    words_found += 1
                else:
                    words_missing += 1
            except Exception as e:
                logger.debug(f"Could not get word {image_id}#{receipt_id}#{line_id}#{word_id}: {e}")
                words_missing += 1

        if words_found > 0:
            logger.debug(f"Loaded {words_found} words, {words_missing} missing")

        return words_by_key

    def load_labels_by_type(
        self,
        label_type: str,
        batch_size: int = 1000,
        max_merchants: Optional[int] = None,
    ) -> int:
        """
        Load labels for a specific label type, processing in batches to avoid memory bloat.

        **Memory-efficient**: Processes labels in batches, groups by merchant incrementally.
        Only keeps one merchant group in memory at a time.

        **Works per label type across all receipts** (not per receipt).
        Uses GSI1 to efficiently query by label type.

        Note: ReceiptWordLabel does NOT contain word text - it only references
        the word via (image_id, receipt_id, line_id, word_id). Word text must
        be fetched separately from ReceiptWord entities.

        Args:
            label_type: CORE_LABEL type (e.g., "GRAND_TOTAL")
            batch_size: Number of labels to process per batch (default: 1000)
            max_merchants: Optional limit on number of merchants to process

        Returns:
            Total number of labels loaded
        """
        logger.info(f"Loading {label_type} labels from DynamoDB (streaming, batch_size={batch_size})...")

        self._merchant_groups = {}
        self._no_merchant_labels = []
        self._label_type = label_type
        total = 0
        merchants_processed = 0

        try:
            # Stream labels in batches
            for batch_num, label_batch in enumerate(self._stream_labels_by_type(label_type)):
                logger.debug(f"Processing batch {batch_num + 1}: {len(label_batch)} labels")

                # Get merchant names for this batch
                metadata_by_key = self._get_merchant_for_labels(label_batch)

                # Get word text for this batch
                # CRITICAL: We need word text to validate labels - same word text should have same label
                words_by_key = self._get_words_for_labels(label_batch)
                words_loaded = len(words_by_key)
                words_expected = len(set((l.image_id, l.receipt_id, l.line_id, l.word_id) for l in label_batch))
                if words_loaded < words_expected:
                    logger.warning(
                        f"Batch {batch_num + 1}: Only loaded {words_loaded}/{words_expected} words "
                        f"({words_expected - words_loaded} missing)"
                    )

                # Create LabelRecord objects and group by merchant
                for label in label_batch:
                    key = (label.image_id, label.receipt_id)
                    merchant_name = metadata_by_key.get(key)

                    word_key = (label.image_id, label.receipt_id, label.line_id, label.word_id)
                    word_text = words_by_key.get(word_key)

                    record = LabelRecord(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=label.label,
                        validation_status=label.validation_status,
                        merchant_name=merchant_name,
                        word_text=word_text,
                    )

                    if merchant_name:
                        group_key = (merchant_name, label_type)
                        if group_key not in self._merchant_groups:
                            # Check if we've hit the merchant limit
                            if max_merchants and merchants_processed >= max_merchants:
                                # Skip this label silently (already logged once per batch)
                                continue
                            self._merchant_groups[group_key] = MerchantLabelGroup(
                                merchant_name=merchant_name,
                                label_type=label_type,
                            )
                            merchants_processed += 1
                            if max_merchants and merchants_processed == max_merchants:
                                logger.info(f"Reached merchant limit ({max_merchants}), will skip remaining labels")

                        # Only add to group if we haven't hit the limit
                        if not max_merchants or merchants_processed <= max_merchants:
                            self._merchant_groups[group_key].labels.append(record)
                    else:
                        self._no_merchant_labels.append(record)

                    total += 1

                # Log progress periodically
                if (batch_num + 1) % 10 == 0:
                    logger.info(
                        f"Processed {batch_num + 1} batches: {total} labels, "
                        f"{len(self._merchant_groups)} merchant groups"
                    )

            logger.info(
                f"Loaded {total} labels: "
                f"{len(self._merchant_groups)} merchant groups, "
                f"{len(self._no_merchant_labels)} without merchant_name"
            )

        except Exception as e:
            logger.error(f"Failed to load labels: {e}")
            raise

        return total

    def _compute_consensus(self, group: MerchantLabelGroup) -> None:
        """
        Compute consensus label for a merchant group.

        The consensus is the most common VALID label among all labels in the group.
        If no VALID labels exist, use the most common label overall.

        **Uses word text for validation**: Groups by similar word text to ensure
        that similar words (e.g., "$25.99", "$25.99", "$25.99") have the same label.
        """
        if not group.labels:
            return

        # Count labels by validation status
        valid_labels: Dict[str, int] = defaultdict(int)
        all_labels: Dict[str, int] = defaultdict(int)

        # Also group by word text to validate that similar words have same label/validation_status
        # Track both label values and validation_statuses per word text
        labels_by_word_text: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        validation_status_by_word_text: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        words_with_text = 0
        words_without_text = 0

        for record in group.labels:
            all_labels[record.label] += 1
            if record.validation_status == "VALID":
                valid_labels[record.label] += 1

            # Group by word text for validation
            if record.word_text:
                words_with_text += 1
                # Normalize word text for comparison (case-insensitive, strip whitespace)
                normalized_text = record.word_text.strip().upper()
                labels_by_word_text[normalized_text][record.label] += 1
                # Track validation_status per word text
                status = record.validation_status or "PENDING"
                validation_status_by_word_text[normalized_text][status] += 1
            else:
                words_without_text += 1

        # Check for word text inconsistencies:
        # 1. Same word text, different label values (shouldn't happen if filtering works correctly)
        # 2. Same word text, different validation_statuses (this is a conflict!)
        word_text_conflicts = []
        for word_text, label_counts in labels_by_word_text.items():
            if len(label_counts) > 1:
                # Same word text has multiple label values - this is a conflict
                word_text_conflicts.append({
                    "word_text": word_text,
                    "conflict_type": "different_labels",
                    "labels": dict(label_counts),
                    "total": sum(label_counts.values())
                })

        # Check for validation_status conflicts (more common)
        for word_text, status_counts in validation_status_by_word_text.items():
            if len(status_counts) > 1:
                # Same word text has multiple validation_statuses - this is a conflict
                word_text_conflicts.append({
                    "word_text": word_text,
                    "conflict_type": "different_validation_statuses",
                    "validation_statuses": dict(status_counts),
                    "total": sum(status_counts.values())
                })

        if word_text_conflicts:
            logger.warning(
                f"Found {len(word_text_conflicts)} word text conflicts for {group.merchant_name} {group.label_type}: "
                f"{word_text_conflicts[:3]}"  # Show first 3 conflicts
            )

        # Prefer VALID labels if available
        if valid_labels:
            group.consensus_label = max(valid_labels.items(), key=lambda x: x[1])[0]
        elif all_labels:
            group.consensus_label = max(all_labels.items(), key=lambda x: x[1])[0]

        # Log word text statistics
        if words_with_text > 0:
            logger.debug(
                f"Consensus for {group.merchant_name} {group.label_type}: "
                f"{words_with_text} words with text, {words_without_text} without, "
                f"{len(word_text_conflicts)} conflicts, consensus={group.consensus_label}"
            )

    def _find_similar_words(
        self,
        group: MerchantLabelGroup,
        similarity_threshold: float = 0.85,
    ) -> None:
        """
        Find similar words using ChromaDB and cluster them.

        Groups labels by semantic similarity using word embeddings.
        """
        if not self.chroma or not self.embed_fn or not group.labels:
            return

        logger.debug(
            f"Finding similar words for {group.merchant_name} {group.label_type} "
            f"({len(group.labels)} labels)"
        )

        # Get embeddings for all words in this group
        chroma_ids = []
        label_records = []
        for record in group.labels:
            chroma_id = _build_chromadb_word_id(
                record.image_id, record.receipt_id, record.line_id, record.word_id
            )
            chroma_ids.append(chroma_id)
            label_records.append((chroma_id, record))

        # Get embeddings from ChromaDB
        try:
            logger.debug(f"Fetching embeddings for {len(chroma_ids)} words from ChromaDB")
            results = self.chroma.get(
                collection_name="words",
                ids=chroma_ids,
                include=["embeddings", "metadatas"],
            )

            if not results or not results.get("ids"):
                logger.warning(f"No embeddings found for {len(chroma_ids)} words in ChromaDB")
                return

            found_ids = results.get("ids", [])
            embeddings = results.get("embeddings", [])
            logger.info(
                f"Retrieved {len(found_ids)}/{len(chroma_ids)} embeddings for similarity clustering "
                f"({len(chroma_ids) - len(found_ids)} missing)"
            )

            # Build embedding map
            embeddings_by_id = {}
            for i, chroma_id in enumerate(found_ids):
                if i < len(embeddings) and embeddings[i] is not None:
                    embeddings_by_id[chroma_id] = embeddings[i]
                else:
                    logger.debug(f"Missing embedding for {chroma_id}")

            logger.debug(f"Built embedding map: {len(embeddings_by_id)} embeddings available")

            # Cluster similar words
            clusters = []
            processed = set()
            comparisons_made = 0

            for chroma_id, record in label_records:
                if chroma_id in processed:
                    continue

                if chroma_id not in embeddings_by_id:
                    logger.debug(f"Skipping '{record.word_text}': no embedding available")
                    continue

                embedding = embeddings_by_id[chroma_id]
                cluster = [record]
                processed.add(chroma_id)

                # Find similar words
                for other_id, other_record in label_records:
                    if other_id in processed or other_id not in embeddings_by_id:
                        continue

                    other_embedding = embeddings_by_id[other_id]
                    comparisons_made += 1

                    # Calculate cosine similarity
                    similarity = self._cosine_similarity(embedding, other_embedding)

                    if similarity >= similarity_threshold:
                        cluster.append(other_record)
                        processed.add(other_id)
                        logger.debug(
                            f"Similar words: '{record.word_text}' and '{other_record.word_text}' "
                            f"(similarity={similarity:.3f})"
                        )

                if len(cluster) > 1:  # Only keep clusters with multiple words
                    clusters.append(cluster)
                    logger.debug(
                        f"Created cluster with {len(cluster)} words: "
                        f"{[r.word_text for r in cluster[:3]]}"
                    )

            group.similarity_clusters = clusters
            logger.info(
                f"Similarity clustering complete: {len(clusters)} clusters found, "
                f"{comparisons_made} comparisons made, "
                f"{len(processed)} words processed"
            )

        except Exception as e:
            logger.error(f"Error finding similar words: {e}", exc_info=True)

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = sum(a * a for a in vec1) ** 0.5
        magnitude2 = sum(b * b for b in vec2) ** 0.5

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    async def _identify_outliers(
        self,
        group: MerchantLabelGroup,
        similarity_threshold: float = 0.70,
    ) -> None:
        """
        Identify words that don't belong in this group (outliers/conflicts).

        Uses ChromaDB query with metadata filters to find similar words:
        1. For each word, query ChromaDB for similar words from the same merchant
        2. Filter by validated_labels metadata to find words with VALID labels
        3. If a word has few/no similar matches with VALID labels, it's an outlier

        Outliers are words that:
        1. Have low similarity to other VALID words in the group
        2. Don't fit the semantic pattern of the group
        3. Are clearly mislabeled (e.g., "$5.99" labeled as GRAND_TOTAL when it's an item price)

        This is where conflicts arise - words that shouldn't have this label.
        """
        if not group.labels or len(group.labels) < 3:
            logger.debug(
                f"Skipping outlier detection: need at least 3 labels, got {len(group.labels)}"
            )
            return

        if not self.chroma or not self.embed_fn:
            logger.debug("Skipping outlier detection: ChromaDB client or embed_fn not available")
            return

        logger.info(
            f"Identifying outliers for {group.merchant_name} {group.label_type} "
            f"({len(group.labels)} labels)"
        )

        # Get embeddings for all words in the group
        chroma_ids = []
        label_records = []
        for record in group.labels:
            chroma_id = _build_chromadb_word_id(
                record.image_id, record.receipt_id, record.line_id, record.word_id
            )
            chroma_ids.append(chroma_id)
            label_records.append((chroma_id, record))

        logger.debug(f"Built {len(chroma_ids)} ChromaDB IDs for outlier detection")

        try:
            # Get embeddings for words in this group
            results = self.chroma.get(
                collection_name="words",
                ids=chroma_ids,
                include=["embeddings", "metadatas"],
            )

            if not results or not results.get("ids"):
                logger.warning(f"No embeddings found for {len(chroma_ids)} words in ChromaDB")
                return

            found_ids = results.get("ids", [])
            embeddings = results.get("embeddings", [])
            metadatas = results.get("metadatas", [])

            logger.info(
                f"Retrieved {len(found_ids)}/{len(chroma_ids)} embeddings from ChromaDB "
                f"({len(found_ids) - len(chroma_ids)} missing)"
            )

            # Build embedding map
            embeddings_by_id = {}
            for i, chroma_id in enumerate(found_ids):
                if i < len(embeddings) and embeddings[i] is not None:
                    embeddings_by_id[chroma_id] = embeddings[i]
                else:
                    logger.debug(f"Missing embedding for {chroma_id}")

            logger.debug(f"Built embedding map: {len(embeddings_by_id)} embeddings available")

            # Prepare merchant filter for queries
            merchant_filter = group.merchant_name.strip().title() if group.merchant_name else None

            # For each word, query ChromaDB to find similar words with VALID labels
            outliers = []
            words_without_embeddings = 0
            words_checked = 0

            # For large groups, use smarter pre-filtering before LLM calls
            # Since large groups are now split into batches (max 500 labels per batch),
            # we can be more thorough. Still prioritize suspicious labels.
            # Use a two-phase approach:
            # 1. Fast similarity-based filtering (identify likely outliers)
            # 2. LLM validation for edge cases

            # Sort records to prioritize checking INVALID and NEEDS_REVIEW labels first
            # These are more likely to be outliers, but we check ALL labels (including VALID)
            # because harmonization is about consistency with consensus, not validation status.
            # A VALID label that doesn't match consensus is still an outlier.
            sorted_records = sorted(
                label_records,
                key=lambda x: (
                    0 if x[1].validation_status == "INVALID" else
                    1 if x[1].validation_status == "NEEDS_REVIEW" else
                    2  # VALID, PENDING, and other statuses
                )
            )

            # For very large batches (close to 500), still limit LLM calls to prevent timeout
            # But use a higher limit since batches are now capped at 500
            max_llm_checks = min(300, len(group.labels)) if len(group.labels) > 300 else len(group.labels)
            llm_checks_done = 0

            for chroma_id, record in sorted_records:
                # Stop if we've reached the limit
                if llm_checks_done >= max_llm_checks:
                    logger.info(
                        f"Reached LLM check limit ({max_llm_checks}) for large group "
                        f"({len(group.labels)} labels). Remaining words will be skipped."
                    )
                    break
                if chroma_id not in embeddings_by_id:
                    words_without_embeddings += 1
                    logger.debug(
                        f"No embedding for '{record.word_text}' ({chroma_id}), skipping outlier check"
                    )
                    continue

                # Skip VALID labels - we're comparing against VALID labels, so VALID labels
                # that match consensus are already correct. Only check non-VALID labels.
                if record.validation_status == "VALID":
                    logger.debug(
                        f"Skipping VALID label '{record.word_text}' - already validated, "
                        f"only checking non-VALID labels against VALID examples"
                    )
                    continue

                words_checked += 1
                embedding = embeddings_by_id[chroma_id]

                try:
                    # Build where clause to find similar words from same merchant
                    # Note: ChromaDB doesn't support $contains, so we'll filter validated_labels in Python
                    where_clause = None
                    if merchant_filter:
                        where_clause = {"merchant_name": {"$eq": merchant_filter}}

                    # Query ChromaDB for similar words (get more results since we'll filter in Python)
                    # Note: IDs are always returned, don't include in include parameter
                    query_results = self.chroma.query(
                        collection_name="words",
                        query_embeddings=[embedding],
                        n_results=50,  # Get enough results for LLM to analyze
                        where=where_clause,
                        include=["metadatas", "distances"],
                    )

                    # Process results
                    similar_ids = query_results.get("ids", [[]])[0] if query_results else []
                    distances = query_results.get("distances", [[]])[0] if query_results else []
                    metadatas = query_results.get("metadatas", [[]])[0] if query_results else []

                    # Filter results in Python:
                    # 1. Exclude self
                    # 2. Check similarity threshold
                    # 3. Check if validated_labels contains the label type (comma-delimited string)
                    similar_matches = []
                    label_pattern = f",{group.label_type},"  # Match in comma-delimited string

                    for idx, similar_id in enumerate(similar_ids):
                        if similar_id == chroma_id:
                            continue  # Skip self

                        if idx >= len(distances) or idx >= len(metadatas):
                            continue

                        # Convert distance to similarity (ChromaDB uses L2 distance)
                        # For normalized embeddings: similarity ≈ 1 - (distance / 2)
                        similarity = max(0.0, 1.0 - (distances[idx] / 2))
                        if similarity < similarity_threshold:
                            continue

                        # Check if this word has VALID label of the target type
                        metadata = metadatas[idx] if metadatas else {}
                        validated_labels_str = metadata.get("validated_labels", "")

                        # validated_labels is stored as ",GRAND_TOTAL,SUBTOTAL," format
                        if label_pattern in validated_labels_str:
                            similar_matches.append((similar_id, similarity))
                            logger.debug(
                                f"  Match: {similar_id} (sim={similarity:.3f}, "
                                f"validated_labels={validated_labels_str})"
                            )

                    logger.debug(
                        f"Word '{record.word_text}': found {len(similar_matches)} similar words "
                        f"with VALID {group.label_type} labels "
                        f"(threshold={similarity_threshold})"
                    )

                    # Use LLM to determine if this word is an outlier
                    # LLM call with retry logic - will raise exception if all retries fail
                    try:
                        llm_checks_done += 1
                        is_outlier = await self._llm_determine_outlier(
                            word=record,
                            similar_matches=similar_matches,
                            group=group,
                            metadatas=metadatas,
                            similar_ids=similar_ids,
                        )

                        if is_outlier:
                            outliers.append(record)
                            logger.warning(
                                f"OUTLIER DETECTED (LLM): '{record.word_text}' does not belong in "
                                f"{group.label_type} group for {group.merchant_name}. "
                                f"Status: {record.validation_status or 'PENDING'}"
                            )
                    except Exception as e:
                        # LLM call failed after all retries - log error and skip this word
                        # This is explicit failure handling, not silent
                        logger.error(
                            f"Failed to determine outlier status for '{record.word_text}' "
                            f"after retries: {e}. Skipping this word."
                        )
                        # Skip this word - we can't make a decision without LLM
                        continue

                except Exception as e:
                    logger.warning(
                        f"Error querying similar words for '{record.word_text}' ({chroma_id}): {e}"
                    )
                    continue

            group.outliers = outliers

            logger.info(
                f"Outlier detection complete for {group.merchant_name} {group.label_type}: "
                f"{len(outliers)} outliers found, "
                f"{words_checked} words checked, "
                f"{words_without_embeddings} words without embeddings"
            )

            if outliers:
                outlier_details = [
                    f"'{o.word_text}' ({o.validation_status or 'PENDING'})"
                    for o in outliers[:10]
                ]
                logger.warning(
                    f"Found {len(outliers)} outliers in {group.merchant_name} {group.label_type}: "
                    f"{outlier_details}"
                )

                # Check if ALL labels are outliers - this suggests metadata is wrong
                if len(outliers) == len(group.labels) and len(group.labels) > 0:
                    outlier_word_texts = set(o.word_text.upper().strip() for o in outliers if o.word_text)
                    merchant_name_words = set(group.merchant_name.upper().replace("-", " ").replace("&", " ").split())

                    # If none of the outlier words match the merchant name, metadata is likely wrong
                    if outlier_word_texts and not any(word in merchant_name_words for word in outlier_word_texts):
                        logger.error(
                            f"⚠️  METADATA MISMATCH DETECTED: All {len(outliers)} labels in "
                            f"{group.merchant_name} {group.label_type} group are outliers. "
                            f"Outlier words: {sorted(outlier_word_texts)}. "
                            f"This suggests ReceiptMetadata.merchant_name='{group.merchant_name}' "
                            f"may be incorrect for receipt(s) with word text: {sorted(outlier_word_texts)}"
                        )

        except Exception as e:
            logger.error(f"Error identifying outliers: {e}", exc_info=True)

    @traceable
    async def _llm_determine_outlier(
        self,
        word: LabelRecord,
        similar_matches: List[Tuple[str, float]],
        group: MerchantLabelGroup,
        metadatas: List[Dict],
        similar_ids: List[str],
        run_tree: Optional[Any] = None,  # Auto-populated by @traceable decorator
    ) -> bool:
        """
        Use LLM to determine if a word is an outlier in the group.

        Relies solely on LLM - no heuristic fallback. Retries on rate limits
        and transient errors following Ollama/LangGraph best practices.

        Args:
            word: The word being evaluated
            similar_matches: List of (chroma_id, similarity) tuples for similar words with VALID labels
            group: The merchant label group
            metadatas: Metadata for all similar words found
            similar_ids: IDs of all similar words found

        Returns:
            True if the word is an outlier, False otherwise

        Raises:
            Exception: If LLM is not available or all retries fail
        """
        if not self.llm:
            raise ValueError(
                "LLM not available for outlier detection. "
                "Label harmonizer requires LLM - no heuristic fallback."
            )

        # Fetch line context for better decision-making
        line_context = None
        surrounding_words = None
        surrounding_lines = None
        try:
            if self.dynamo and word.image_id and word.receipt_id and word.line_id:
                # Get the full line text
                line = self.dynamo.get_receipt_line(
                    receipt_id=word.receipt_id,
                    image_id=word.image_id,
                    line_id=word.line_id
                )
                line_context = line.text if line else None

                # Get words in the line to show surrounding context
                words_in_line = self.dynamo.list_receipt_words_from_line(
                    receipt_id=word.receipt_id,
                    image_id=word.image_id,
                    line_id=word.line_id
                )
                if words_in_line:
                    # Sort words by word_id to ensure correct order
                    words_in_line.sort(key=lambda w: w.word_id)
                    # Find the target word and get surrounding words (3 before, 3 after)
                    word_texts = [w.text for w in words_in_line]
                    try:
                        word_index = next(
                            i for i, w in enumerate(words_in_line)
                            if w.word_id == word.word_id
                        )
                        start_idx = max(0, word_index - 3)
                        end_idx = min(len(word_texts), word_index + 4)
                        surrounding_words = word_texts[start_idx:end_idx]
                        # Mark the target word
                        target_idx_in_context = word_index - start_idx
                        if surrounding_words:
                            surrounding_words[target_idx_in_context] = f"[{surrounding_words[target_idx_in_context]}]"
                    except StopIteration:
                        # Word not found in line, use full line text
                        surrounding_words = word_texts

                # Get all lines from receipt and format them to show how they appear on the receipt
                # This matches the pattern from receipt_label/completion/_format_prompt.py _format_receipt_lines
                all_receipt_lines = self.dynamo.list_receipt_lines_from_receipt(
                    image_id=word.image_id,
                    receipt_id=word.receipt_id
                )
                if all_receipt_lines and line:
                    # Sort lines by Y-coordinate (top to bottom) first
                    sorted_lines = sorted(all_receipt_lines, key=lambda l: l.calculate_centroid()[1])

                    # Format lines to show how they appear on the receipt
                    # Group visually contiguous lines (on same row) together using corners
                    # This matches _format_receipt_lines logic: check if centroid Y is within
                    # the previous line's vertical span (between bottom_left and top_left)
                    formatted_lines = []
                    for i, receipt_line in enumerate(sorted_lines):
                        # Check if this line is on the same visual row as the previous line
                        if i > 0:
                            prev_line = sorted_lines[i - 1]
                            curr_centroid = receipt_line.calculate_centroid()
                            # Check if current line's centroid Y is within previous line's vertical span
                            # Using the same logic as _format_receipt_lines
                            if prev_line.bottom_left["y"] < curr_centroid[1] < prev_line.top_left["y"]:
                                # Same visual row - append to previous formatted line with space
                                if receipt_line.line_id == word.line_id:
                                    formatted_lines[-1] += f" [{receipt_line.text}]"
                                else:
                                    formatted_lines[-1] += f" {receipt_line.text}"
                                continue

                        # New row - add as new line
                        if receipt_line.line_id == word.line_id:
                            formatted_lines.append(f"[{receipt_line.text}]")
                        else:
                            formatted_lines.append(receipt_line.text)

                    surrounding_lines = formatted_lines
        except Exception as e:
            logger.debug(f"Could not fetch line context for word {word.word_text}: {e}")
            # Continue without context - not critical

        # Build context about similar words, including their line context
        similar_words_info = []
        for similar_id, similarity in similar_matches[:10]:  # Limit to top 10
            # Find metadata for this similar word
            similar_idx = similar_ids.index(similar_id) if similar_id in similar_ids else -1
            if similar_idx >= 0 and similar_idx < len(metadatas):
                metadata = metadatas[similar_idx]

                # Fetch line context for this similar word to help LLM understand usage
                similar_line_context = None
                similar_surrounding_words = None
                similar_surrounding_lines = None
                try:
                    if self.dynamo and metadata.get("image_id") and metadata.get("receipt_id") and metadata.get("line_id"):
                        similar_image_id = metadata.get("image_id")
                        similar_receipt_id = int(metadata.get("receipt_id", 0))
                        similar_line_id = int(metadata.get("line_id", 0))

                        similar_line = self.dynamo.get_receipt_line(
                            receipt_id=similar_receipt_id,
                            image_id=similar_image_id,
                            line_id=similar_line_id
                        )
                        similar_line_context = similar_line.text if similar_line else None

                        # Get surrounding words for the similar word
                        similar_words_in_line = self.dynamo.list_receipt_words_from_line(
                            receipt_id=similar_receipt_id,
                            image_id=similar_image_id,
                            line_id=similar_line_id
                        )
                        if similar_words_in_line and metadata.get("word_id"):
                            # Sort words by word_id to ensure correct order
                            similar_words_in_line.sort(key=lambda w: w.word_id)
                            similar_word_id = int(metadata.get("word_id", 0))
                            try:
                                similar_word_index = next(
                                    i for i, w in enumerate(similar_words_in_line)
                                    if w.word_id == similar_word_id
                                )
                                similar_word_texts = [w.text for w in similar_words_in_line]
                                start_idx = max(0, similar_word_index - 3)
                                end_idx = min(len(similar_word_texts), similar_word_index + 4)
                                similar_surrounding_words = similar_word_texts[start_idx:end_idx]
                                # Mark the target word
                                target_idx = similar_word_index - start_idx
                                if similar_surrounding_words and target_idx < len(similar_surrounding_words):
                                    similar_surrounding_words[target_idx] = f"[{similar_surrounding_words[target_idx]}]"
                            except (StopIteration, ValueError):
                                # Word not found in line, use full line text
                                similar_surrounding_words = [w.text for w in similar_words_in_line]

                        # Get all lines from receipt and format them to show how they appear on the receipt
                        # This matches the pattern from receipt_label/completion/_format_prompt.py _format_receipt_lines
                        all_similar_receipt_lines = self.dynamo.list_receipt_lines_from_receipt(
                            image_id=similar_image_id,
                            receipt_id=similar_receipt_id
                        )
                        if all_similar_receipt_lines and similar_line:
                            # Sort lines by Y-coordinate (top to bottom) first
                            sorted_similar_lines = sorted(all_similar_receipt_lines, key=lambda l: l.calculate_centroid()[1])

                            # Format lines to show how they appear on the receipt
                            # Group visually contiguous lines (on same row) together using corners
                            # This matches _format_receipt_lines logic: check if centroid Y is within
                            # the previous line's vertical span (between bottom_left and top_left)
                            formatted_lines = []
                            for i, receipt_line in enumerate(sorted_similar_lines):
                                # Check if this line is on the same visual row as the previous line
                                if i > 0:
                                    prev_line = sorted_similar_lines[i - 1]
                                    curr_centroid = receipt_line.calculate_centroid()
                                    # Check if current line's centroid Y is within previous line's vertical span
                                    # Using the same logic as _format_receipt_lines
                                    if prev_line.bottom_left["y"] < curr_centroid[1] < prev_line.top_left["y"]:
                                        # Same visual row - append to previous formatted line with space
                                        if receipt_line.line_id == similar_line_id:
                                            formatted_lines[-1] += f" [{receipt_line.text}]"
                                        else:
                                            formatted_lines[-1] += f" {receipt_line.text}"
                                        continue

                                # New row - add as new line
                                if receipt_line.line_id == similar_line_id:
                                    formatted_lines.append(f"[{receipt_line.text}]")
                                else:
                                    formatted_lines.append(receipt_line.text)

                            similar_surrounding_lines = formatted_lines
                except Exception as e:
                    logger.debug(f"Could not fetch line context for similar word {similar_id}: {e}")
                    # Continue without context - not critical

                similar_words_info.append({
                    "text": metadata.get("text", "unknown"),
                    "similarity": f"{similarity:.3f}",
                    "validated_labels": metadata.get("validated_labels", ""),
                    "line_context": similar_line_context,
                    "surrounding_words": similar_surrounding_words,
                    "surrounding_lines": similar_surrounding_lines,
                })

        # Extract individual words from merchant name for context
        merchant_words = set()
        if group.merchant_name:
            # Split by common separators and normalize
            for part in group.merchant_name.replace("-", " ").replace("&", " ").split():
                cleaned = part.strip().upper()
                if cleaned:
                    merchant_words.add(cleaned)

        # Get label type definition
        label_definition = CORE_LABELS.get(group.label_type, "N/A")

        # Build prompt for LLM with markdown-friendly formatting
        context_section = f"""## Context

- **Merchant:** {group.merchant_name}
- **Label Type:** `{group.label_type}`
- **Label Type Definition:** {label_definition}
- **Word being evaluated:** `"{word.word_text}"`
- **Current validation status:** {word.validation_status or 'PENDING'}"""

        # Add merchant name components as a hint, but emphasize context is primary
        # Only relevant for MERCHANT_NAME label type
        if merchant_words and group.label_type == "MERCHANT_NAME":
            context_section += f"\n- **Note:** The merchant name contains these components: {sorted(merchant_words)}. These may be valid when appearing in merchant name context (header/store info area), but **context is the primary factor** - consider receipt position and surrounding text carefully."""

        # Add line context if available
        if line_context:
            context_section += f"\n- **Full line text:** `\"{line_context}\"`"
        if surrounding_words:
            context_section += f"\n- **Surrounding words:** `{' '.join(surrounding_words)}`"
        if surrounding_lines:
            context_section += f"\n- **Surrounding lines** (spatial order, target line marked):\n  ```\n  " + "\n  ".join(surrounding_lines) + "\n  ```"

        prompt = f"""# Receipt Label Validation

You are analyzing receipt word labels for consistency. Your task is to determine if a word belongs in a group of words with a specific label type.

{context_section}

## Similar Words with VALID `{group.label_type}` Labels

"""
        if similar_words_info:
            for i, info in enumerate(similar_words_info, 1):
                prompt += f"### Example {i}\n\n"
                prompt += f"- **Text:** `'{info['text']}'`\n"
                prompt += f"- **Similarity:** {info['similarity']}\n"
                prompt += f"- **Labels:** {info['validated_labels']}\n"
                if info.get('line_context'):
                    prompt += f"- **Line context:** `\"{info['line_context']}\"`\n"
                if info.get('surrounding_words'):
                    prompt += f"- **Surrounding words:** `{' '.join(info['surrounding_words'])}`\n"
                if info.get('surrounding_lines'):
                    prompt += f"- **Surrounding lines** (spatial order, target line marked):\n  ```\n  " + "\n  ".join(info['surrounding_lines']) + "\n  ```\n"
                prompt += "\n"
        else:
            prompt += "None found.\n\n"

        prompt += f"""
## Question

Does the word `"{word.word_text}"` belong in this group of words with the label `{group.label_type}`?

## Evaluation Criteria

**IMPORTANT: Evaluate context FIRST, then consider merchant name components as a secondary hint.**

1. **Receipt context and position (PRIMARY):**
   - Where does this word appear on the receipt? (header/store info area vs product descriptions vs totals)
   - What text surrounds it? Does the surrounding context indicate merchant/store information or product descriptions?
   - Does the spatial position (top of receipt, near store address/phone) suggest merchant name context?

2. **Pattern consistency:** Does it match the pattern of other VALID `{group.label_type}` words in the similar examples provided?

3. **Semantic similarity:** Does the word text make sense as a `{group.label_type}` in this specific context?

4. **Similarity scores:** Does it have enough similar matches with VALID labels from the same merchant?

5. **OCR/scanning errors:** Receipt text may have word boundary issues, missing characters, or character substitutions. If a word appears to be a variant or partial match, verify it's in the correct context before validating."""

        # Add merchant name component criteria only for MERCHANT_NAME label type
        if group.label_type == "MERCHANT_NAME":
            prompt += f"""
6. **Merchant name components (SECONDARY HINT):**
   - Multi-word merchant names may have individual components that appear separately on receipts
   - These components are valid **ONLY when appearing in merchant name context** (header area, store information section)
   - Words matching merchant name components but appearing in product descriptions, line items, or other non-merchant contexts are likely **NOT valid**
   - **Do NOT rely solely on merchant name component matching** - always verify the receipt context first

7. **Merchant name in product descriptions:** When a merchant name word appears within a product description, it may still be valid as `MERCHANT_NAME` if it's clearly functioning as a brand identifier. However, if it's clearly part of a product name and not functioning as merchant identification, it may not be valid."""

        prompt += f"""

## Examples of Outliers

- Label text like 'AMOUNT:' or 'TOTAL:' incorrectly labeled as values
- Completely unrelated words that don't match the semantic pattern
- Values that are clearly wrong (e.g., small amounts labeled as GRAND_TOTAL when other totals are much larger)
- Words in the wrong receipt section"""

        # Add MERCHANT_NAME-specific outlier examples
        if group.label_type == "MERCHANT_NAME":
            prompt += """
- Words matching merchant name components but appearing in product descriptions or line items (context mismatch)
- Words in the totals/payment section incorrectly labeled as merchant name
- Words in clearly non-merchant contexts (e.g., product names, quantities, prices)"""

        prompt += f"""

## Examples of Valid Labels

- Words that match the semantic pattern of other VALID labels in the group
- OCR variants or partial words that clearly relate to the label type
- Values that appear in similar context/position as other valid labels"""

        # Add MERCHANT_NAME-specific examples
        if group.label_type == "MERCHANT_NAME":
            prompt += """
- Words appearing in merchant name context (header area, near store address/phone, store hours)
- Merchant name words functioning as brand identifiers in product descriptions (when clearly identifying the merchant, not just part of a product name)"""

        prompt += f"""

## Response Format

Respond with a JSON object indicating whether the word belongs in this group.
- `is_outlier`: boolean (true if the word does NOT belong, false if it belongs)
- `reasoning`: optional string explaining your decision"""

        # Retry logic following Ollama/LangGraph best practices
        max_retries = 3
        retry_delay = 2.0  # Base delay in seconds (exponential backoff)
        last_error = None

        for attempt in range(max_retries):
            try:
                from langchain_core.messages import HumanMessage
                from langchain_core.runnables import RunnableConfig

                messages = [HumanMessage(content=prompt)]

                # Log the full prompt for debugging (especially for merchant name components)
                logger.info(
                    "LLM Prompt for '%s' in %s %s:\n%s\n%s\n%s",
                    word.word_text,
                    group.merchant_name,
                    group.label_type,
                    "=" * 80,
                    prompt,
                    "=" * 80,
                )

                # Add LangSmith metadata for prompt comparison
                # This helps track different prompt versions and compare results
                # Include full receipt context for precise filtering
                tags = [
                    "label-harmonizer",
                    f"merchant:{group.merchant_name}",
                    f"label-type:{group.label_type}",
                    "outlier-detection",
                ]
                if word.image_id:
                    tags.append(f"image:{word.image_id}")
                if word.receipt_id:
                    tags.append(f"receipt:{word.receipt_id}")

                config = RunnableConfig(
                    metadata={
                        "prompt_version": "v2-markdown-full-context",
                        "merchant": group.merchant_name,
                        "label_type": group.label_type,
                        "word": word.word_text,
                        "image_id": word.image_id or "",
                        "receipt_id": str(word.receipt_id) if word.receipt_id else "",
                        "line_id": str(word.line_id) if word.line_id else "",
                        "word_id": str(word.word_id) if word.word_id else "",
                        "validation_status": word.validation_status or "PENDING",
                        "has_full_receipt_context": bool(surrounding_lines),
                        "has_similar_words": len(similar_words_info) > 0,
                    },
                    tags=tags,
                )

                # Use structured output for reliable parsing
                # Best practice: Pass Pydantic schema to format= parameter
                # This tells Ollama the exact structure expected
                from receipt_agent.tools.label_harmonizer_models import OutlierDecision

                # Create LLM with schema in format parameter (Ollama best practice)
                json_schema = OutlierDecision.model_json_schema()
                llm_with_schema = ChatOllama(
                    model=self.llm.model,
                    base_url=self.llm.base_url,
                    client_kwargs=self.llm.client_kwargs,
                    format=json_schema,  # Pass schema directly to format
                    temperature=self.llm.temperature,
                )
                llm_structured = llm_with_schema.with_structured_output(OutlierDecision)

                # Wrap the LangChain call with tracing_context to link it to this function's trace
                # The @traceable decorator provides the run_tree parameter automatically
                if run_tree and ls:
                    with ls.tracing_context(parent=run_tree):
                        structured_response: OutlierDecision = await llm_structured.ainvoke(messages, config=config)  # type: ignore[assignment]
                else:
                    structured_response: OutlierDecision = await llm_structured.ainvoke(messages, config=config)  # type: ignore[assignment]

                # Store the run tree for later use in child calls
                # The @traceable decorator provides this run_tree parameter
                if run_tree:
                    word_key = f"{word.image_id}:{word.receipt_id}:{word.line_id}:{word.word_id}"
                    group.outlier_run_trees[word_key] = run_tree

                # Extract structured response
                is_outlier = structured_response.is_outlier
                reasoning = structured_response.reasoning or ""

                # Log the full response for debugging
                logger.info(
                    "LLM Response for '%s' in %s %s:\n%s\nIs Outlier: %s\nReasoning: %s\n%s",
                    word.word_text,
                    group.merchant_name,
                    group.label_type,
                    "=" * 80,
                    is_outlier,
                    reasoning,
                    "=" * 80,
                )

                return is_outlier

            except Exception as e:
                last_error = e
                error_str = str(e)

                # Check if it's a retryable error (rate limit, server error, timeout)
                is_retryable = (
                    "429" in error_str or  # Rate limit
                    "500" in error_str or  # Internal server error
                    "503" in error_str or  # Service unavailable
                    "502" in error_str or  # Bad gateway
                    "timeout" in error_str.lower() or
                    "rate limit" in error_str.lower() or
                    "rate_limit" in error_str.lower() or
                    "too many requests" in error_str.lower() or
                    "service unavailable" in error_str.lower() or
                    "internal server error" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff: 2s, 4s, 6s
                    wait_time = retry_delay * (attempt + 1)
                    logger.warning(
                        f"Retryable error for '{word.word_text}' "
                        f"(attempt {attempt + 1}/{max_retries}): {error_str[:100]}. "
                        f"Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Not retryable or max retries reached
                    if attempt >= max_retries - 1:
                        logger.error(
                            f"LLM call failed after {max_retries} attempts for '{word.word_text}': {error_str}"
                        )
                        # Raise exception - no silent failure
                        raise RuntimeError(
                            f"Failed to get LLM decision for '{word.word_text}' after {max_retries} retries: {error_str}"
                        ) from e
                    else:
                        # Not retryable error - raise immediately
                        logger.error(
                            f"Non-retryable error for '{word.word_text}': {error_str}"
                        )
                        raise RuntimeError(
                            f"Non-retryable error calling LLM for '{word.word_text}': {error_str}"
                        ) from e

        # Should never reach here, but just in case
        raise RuntimeError(
            f"Unexpected error: Failed to get LLM decision for '{word.word_text}'"
        ) from last_error

    @traceable
    async def _suggest_label_type_for_outlier(
        self,
        word: LabelRecord,
        group: MerchantLabelGroup,
        run_tree: Optional[Any] = None,  # Auto-populated by @traceable decorator
    ) -> Optional[str]:
        """
        Use LLM to suggest the correct CORE_LABEL type for an outlier.

        For example, if "SPROUTS" in "SPROUTS CREAM CHEESE" is flagged as an outlier
        in MERCHANT_NAME, this will suggest PRODUCT_NAME as the correct label type.

        Args:
            word: The word that is an outlier
            group: The merchant label group it doesn't belong to

        Returns:
            Suggested CORE_LABEL type (e.g., "PRODUCT_NAME") or None if couldn't determine
        """
        if not self.llm:
            logger.warning("LLM not available for label type suggestion for '%s'", word.word_text)
            return None
        if not word.word_text:
            logger.warning("Word text is empty for label type suggestion")
            return None

        # Get line context
        line_context = None
        surrounding_lines = None
        try:
            if self.dynamo and word.image_id and word.receipt_id and word.line_id:
                line = self.dynamo.get_receipt_line(
                    receipt_id=word.receipt_id,
                    image_id=word.image_id,
                    line_id=word.line_id
                )
                line_context = line.text if line else None

                # Get full receipt context
                all_receipt_lines = self.dynamo.list_receipt_lines_from_receipt(
                    image_id=word.image_id,
                    receipt_id=word.receipt_id
                )
                if all_receipt_lines and line:
                    sorted_lines = sorted(all_receipt_lines, key=lambda l: l.calculate_centroid()[1])
                    formatted_lines = []
                    for i, receipt_line in enumerate(sorted_lines):
                        if i > 0:
                            prev_line = sorted_lines[i - 1]
                            curr_centroid = receipt_line.calculate_centroid()
                            if prev_line.bottom_left["y"] < curr_centroid[1] < prev_line.top_left["y"]:
                                if receipt_line.line_id == word.line_id:
                                    formatted_lines[-1] += f" [{receipt_line.text}]"
                                else:
                                    formatted_lines[-1] += f" {receipt_line.text}"
                                continue
                        if receipt_line.line_id == word.line_id:
                            formatted_lines.append(f"[{receipt_line.text}]")
                        else:
                            formatted_lines.append(receipt_line.text)
                    surrounding_lines = formatted_lines
        except Exception as e:
            logger.debug("Could not fetch context for label type suggestion: %s", e)

        # Build prompt with all CORE_LABELS definitions
        core_labels_text = "\n".join(
            f"- **{label}**: {definition}"
            for label, definition in CORE_LABELS.items()
        )

        # Get existing labels for this word to inform the suggestion
        existing_labels = []
        existing_label_types = set()
        if self.dynamo and word.image_id and word.receipt_id and word.line_id and word.word_id:
            try:
                existing_labels_result = self.dynamo.list_receipt_word_labels_for_word(
                    image_id=word.image_id,
                    receipt_id=word.receipt_id,
                    line_id=word.line_id,
                    word_id=word.word_id,
                )
                existing_labels = existing_labels_result[0]  # Get the list from tuple
                existing_label_types = {
                    lbl.label for lbl in existing_labels
                    if lbl.validation_status in ["VALID", "PENDING"]
                }
                if existing_label_types:
                    logger.debug(
                        "Word '%s' already has labels: %s",
                        word.word_text,
                        existing_label_types,
                    )
            except Exception as e:
                logger.debug("Could not fetch existing labels for word: %s", e)

        # Build prompt with existing labels info
        existing_labels_section = ""
        if existing_label_types:
            existing_labels_section = f"\n## Existing Labels for This Word\n\n"
            existing_labels_section += f"This word already has the following label types: {', '.join(sorted(existing_label_types))}\n"
            existing_labels_section += f"Consider whether the suggested label type should be one of these, or if a new label type is needed.\n"

        prompt = f"""# Determine Correct Label Type for Outlier

A word has been identified as an outlier in the `{group.label_type}` label type group.
Your task is to determine the correct CORE_LABEL type for this word.

## Context

- **Merchant:** {group.merchant_name}
- **Word:** `"{word.word_text}"`
- **Current (incorrect) label type:** `{group.label_type}`
- **Line text:** `"{line_context or 'N/A'}"`
- **Full receipt context:**
  ```
  {"\n  ".join(surrounding_lines) if surrounding_lines else "N/A"}
  ```
{existing_labels_section}
## Available CORE_LABEL Types

{core_labels_text}

## Question

What is the correct CORE_LABEL type for the word `"{word.word_text}"` given the context above?

Consider:
- The word's position and context on the receipt
- The semantic meaning of the word
- The definitions of each CORE_LABEL type
- Common receipt patterns

## Response Format

Respond with a JSON object indicating the correct CORE_LABEL type for this word.
- `suggested_label_type`: string or null (the CORE_LABEL type name, or null if it doesn't match any CORE_LABEL type)
- `reasoning`: optional string explaining why this label type was suggested
"""

        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.runnables import RunnableConfig
            from receipt_agent.tools.label_harmonizer_models import LabelTypeSuggestion
            messages = [HumanMessage(content=prompt)]

            # LangSmith best practice: Use run_name for better trace identification
            # According to LangSmith docs, for LangChain runnables, we should pass the parent config
            # However, since we're calling from a non-runnable context, we use langsmith_extra
            # to link the child trace to the parent run tree
            config_dict = {
                "run_name": "suggest_label_type_for_outlier",
                "metadata": {
                    "prompt_version": "v2-markdown-full-context",
                    "task": "suggest_label_type",
                    "merchant": group.merchant_name,
                    "word": word.word_text,
                    "current_label_type": group.label_type,
                    "image_id": word.image_id or "",
                    "receipt_id": str(word.receipt_id) if word.receipt_id else "",
                    "line_id": str(word.line_id) if word.line_id else "",
                    "word_id": str(word.word_id) if word.word_id else "",
                    "existing_label_types": ",".join(sorted(existing_label_types)) if existing_label_types else "",
                },
                "tags": [
                    "label-harmonizer",
                    "label-type-suggestion",
                    "outlier",
                    f"merchant:{group.merchant_name}",
                    f"current-label-type:{group.label_type}",
                ],
            }

            # Create LLM with schema in format parameter (Ollama best practice)
            json_schema = LabelTypeSuggestion.model_json_schema()
            llm_with_schema = ChatOllama(
                model=self.llm.model,
                base_url=self.llm.base_url,
                client_kwargs=self.llm.client_kwargs,
                format=json_schema,  # Pass schema directly to format
                temperature=self.llm.temperature,
            )
            # LangSmith best practice: Use ainvoke for async functions to ensure proper trace context
            llm_structured = llm_with_schema.with_structured_output(LabelTypeSuggestion)

            # According to LangSmith docs, for async functions decorated with @traceable,
            # we should wrap child calls with tracing_context(parent=run_tree) to ensure
            # proper trace nesting. This guarantees that all async calls share the same RunTree.
            child_config = RunnableConfig(**config_dict)

            # Wrap the LangChain runnable call with tracing_context to link to parent
            # The @traceable decorator provides the run_tree parameter automatically
            if run_tree and ls:
                with ls.tracing_context(parent=run_tree):
                    structured_response: LabelTypeSuggestion = await llm_structured.ainvoke(messages, config=child_config)  # type: ignore[assignment]
            else:
                structured_response: LabelTypeSuggestion = await llm_structured.ainvoke(messages, config=child_config)  # type: ignore[assignment]

            # Extract structured response
            suggested_type = structured_response.suggested_label_type
            reasoning = structured_response.reasoning or ""

            # Validate it's a CORE_LABEL
            if suggested_type and suggested_type in CORE_LABELS:
                logger.info(
                    "Suggested label type for outlier '%s': %s (reasoning: %s)",
                    word.word_text,
                    suggested_type,
                    reasoning,
                )
                return suggested_type
            elif suggested_type is None:
                logger.info("No CORE_LABEL type suggested for '%s'", word.word_text)
                return None
            else:
                logger.warning(
                    "Invalid label type suggested: %s (not in CORE_LABELS)",
                    suggested_type,
                )
                return None

        except Exception as e:
            logger.warning("Failed to suggest label type for '%s': %s", word.word_text, e)
            return None

    async def analyze_group(
        self,
        group: MerchantLabelGroup,
        use_similarity: bool = True,
    ) -> list[HarmonizerResult]:
        """
        Analyze a merchant group and return results for each label.

        Args:
            group: The merchant group to analyze
            use_similarity: Whether to use ChromaDB similarity clustering

        Returns:
            List of HarmonizerResult for each label in the group
        """
        results = []

        # Step 1: Compute consensus within the group
        self._compute_consensus(group)

        # Step 2: Find similar words (if enabled)
        if use_similarity:
            self._find_similar_words(group)

        # Step 2.5: Identify outliers - words that don't belong in this group
        await self._identify_outliers(group)

        # Step 3: Determine canonical label
        canonical_label = group.consensus_label

        if not canonical_label:
            # No consensus found, mark all as needing review
            for record in group.labels:
                results.append(HarmonizerResult(
                    image_id=record.image_id,
                    receipt_id=record.receipt_id,
                    line_id=record.line_id,
                    word_id=record.word_id,
                    merchant_name=group.merchant_name,
                    label_type=group.label_type,
                    current_label=record.label,
                    consensus_label=None,
                    needs_update=False,
                    changes_needed=["No consensus found"],
                    confidence=0.0,
                    group_size=len(group.labels),
                    cluster_size=1,
                ))
            return results

        # Step 4: Check each label against canonical label
        for record in group.labels:
            changes = []
            suggested_label_type = None

            # Check if this is an outlier (doesn't belong in the group)
            is_outlier = record in group.outliers
            if is_outlier:
                changes.append(f"OUTLIER: Word '{record.word_text}' doesn't belong in {group.label_type} group")
                # For outliers, suggest the correct label type using LLM
                logger.info(
                    "Outlier detected for '%s' in %s %s. Calling _suggest_label_type_for_outlier...",
                    record.word_text,
                    group.merchant_name,
                    group.label_type,
                )
                # Get the stored run tree from the outlier detection call
                # According to LangSmith docs, for async functions with @traceable,
                # we should pass the parent run_tree either:
                # 1. As a keyword argument: run_tree=parent_run_tree
                # 2. Via langsmith_extra: langsmith_extra={"parent": parent_run_tree}
                word_key = f"{record.image_id}:{record.receipt_id}:{record.line_id}:{record.word_id}"
                parent_run_tree = group.outlier_run_trees.get(word_key)
                if not parent_run_tree:
                    logger.debug(
                        "No run tree found for outlier '%s' - child trace may not be linked",
                        record.word_text,
                    )
                # Pass run_tree to link child trace to parent
                # The @traceable decorator will use this to nest the trace
                suggested_label_type = await self._suggest_label_type_for_outlier(
                    record, group, run_tree=parent_run_tree
                )
                logger.info(
                    "Label type suggestion for '%s': %s",
                    record.word_text,
                    suggested_label_type or "None",
                )

            # Check if label matches consensus
            if record.label != canonical_label:
                changes.append(f"label: {record.label} → {canonical_label}")

            # Calculate confidence based on group size and validation status
            confidence = min(100.0, len(group.labels) * 5)
            if is_outlier:
                # Outliers have low confidence - they don't belong
                confidence = max(0.0, confidence - 50)
            elif record.validation_status == "VALID" and record.label == canonical_label:
                confidence = 100.0
            elif canonical_label and any(
                r.validation_status == "VALID" and r.label == canonical_label
                for r in group.labels
            ):
                confidence = min(100.0, confidence + 30)

            # Find cluster size if in a similarity cluster
            cluster_size = 1
            for cluster in group.similarity_clusters:
                if record in cluster:
                    cluster_size = len(cluster)
                    confidence = min(100.0, confidence + (cluster_size * 2))
                    break

            # Add suggested label type to changes if available
            if suggested_label_type:
                changes.append(f"SUGGESTED_LABEL_TYPE: {suggested_label_type}")

            results.append(HarmonizerResult(
                image_id=record.image_id,
                receipt_id=record.receipt_id,
                line_id=record.line_id,
                word_id=record.word_id,
                merchant_name=group.merchant_name,
                label_type=group.label_type,
                current_label=record.label,
                consensus_label=canonical_label,
                suggested_label_type=suggested_label_type,
                needs_update=len(changes) > 0 or is_outlier,
                changes_needed=changes,
                confidence=confidence,
                group_size=len(group.labels),
                cluster_size=cluster_size,
            ))

        return results

    async def harmonize_label_type(
        self,
        label_type: str,
        use_similarity: bool = True,
        limit: Optional[int] = None,
        batch_size: int = 1000,
        max_merchants: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Harmonize all labels for a specific label type.

        **Works per label type across all receipts** (not per receipt):
        - Loads labels of the specified type in batches (memory-efficient)
        - Groups by merchant_name (e.g., all GRAND_TOTAL labels from "Vons")
        - Finds consensus within each merchant group
        - Identifies outliers that differ from consensus

        **Memory-efficient**: Processes labels in batches to avoid loading
        thousands of labels into memory at once.

        Example:
            If you have 100 receipts from "Vons" with GRAND_TOTAL labels:
            - Loads GRAND_TOTAL labels in batches of 1000
            - Groups them by merchant (same merchant)
            - Finds consensus (most common VALID label)
            - Flags any labels that differ from consensus

        Args:
            label_type: CORE_LABEL type (e.g., "GRAND_TOTAL")
            use_similarity: Whether to use ChromaDB similarity clustering
            limit: Optional limit on number of merchant groups to process
            batch_size: Number of labels to process per batch (default: 1000)
            max_merchants: Optional limit on number of merchants to process

        Returns:
            Report dict with:
            - summary: Stats per merchant group
            - labels: Individual label results
            - no_merchant: Labels without merchant_name (can't be harmonized)
            - stats: Overall statistics
        """
        # Load labels if not already loaded
        if not self._merchant_groups or self._label_type != label_type:
            self.load_labels_by_type(label_type, batch_size=batch_size, max_merchants=max_merchants)

        all_results: list[HarmonizerResult] = []
        group_summaries: list[dict] = []

        # Process each merchant group
        groups_to_process = list(self._merchant_groups.values())
        if limit:
            groups_to_process = groups_to_process[:limit]

        for i, group in enumerate(groups_to_process):
            if i > 0 and i % 10 == 0:
                logger.info(f"Processed {i}/{len(groups_to_process)} groups...")

            results = await self.analyze_group(group, use_similarity=use_similarity)
            all_results.extend(results)

            # Summarize this group
            consistent = sum(1 for r in results if not r.needs_update)
            total = len(results)

            group_summaries.append({
                "merchant_name": group.merchant_name,
                "label_type": group.label_type,
                "total_labels": total,
                "consistent": consistent,
                "needs_update": total - consistent,
                "pct_consistent": (consistent / total * 100) if total > 0 else 0,
                "consensus_label": group.consensus_label,
                "similarity_clusters": len(group.similarity_clusters),
            })

        # Sort summaries by consistency (worst first)
        group_summaries.sort(key=lambda x: (x["pct_consistent"], -x["total_labels"]))

        # Convert results to dicts for JSON serialization
        label_dicts = []
        for r in all_results:
            label_dicts.append({
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "line_id": r.line_id,
                "word_id": r.word_id,
                "merchant_name": r.merchant_name,
                "label_type": r.label_type,
                "current_label": r.current_label,
                "consensus_label": r.consensus_label,
                "suggested_label_type": r.suggested_label_type,
                "needs_update": r.needs_update,
                "changes_needed": r.changes_needed,
                "confidence": r.confidence,
                "group_size": r.group_size,
                "cluster_size": r.cluster_size,
            })

        # No merchant labels
        no_merchant_dicts = []
        for r in self._no_merchant_labels:
            no_merchant_dicts.append({
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "line_id": r.line_id,
                "word_id": r.word_id,
                "label": r.label,
                "validation_status": r.validation_status,
            })

        self._last_report = {
            "summary": group_summaries,
            "labels": label_dicts,
            "no_merchant": no_merchant_dicts,
            "stats": {
                "total_labels": len(all_results) + len(no_merchant_dicts),
                "labels_with_merchant": len(all_results),
                "labels_without_merchant": len(no_merchant_dicts),
                "merchant_groups": len(groups_to_process),
                "label_type": label_type,
            },
        }

        return self._last_report

    async def apply_fixes(
        self,
        dry_run: bool = True,
        min_confidence: float = 70.0,
        min_group_size: int = 3,
    ) -> UpdateResult:
        """
        Apply harmonization fixes to DynamoDB.

        Updates labels to match consensus values.

        Args:
            dry_run: If True, only report what would be updated (no actual writes)
            min_confidence: Minimum confidence to apply fix (0-100)
            min_group_size: Minimum group size to apply fix (larger = more confident)

        Returns:
            UpdateResult with counts and any errors
        """
        if not self._last_report:
            raise ValueError("Must call harmonize_label_type() first")

        result = UpdateResult()
        labels_to_update = []

        # Filter to labels that need updates and meet thresholds
        for r in self._last_report["labels"]:
            if not r["needs_update"]:
                result.total_skipped += 1
                continue

            if r["confidence"] < min_confidence:
                logger.debug(
                    f"Skipping {r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: "
                    f"confidence {r['confidence']} < {min_confidence}"
                )
                result.total_skipped += 1
                continue

            if r["group_size"] < min_group_size:
                logger.debug(
                    f"Skipping {r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: "
                    f"group_size {r['group_size']} < {min_group_size}"
                )
                result.total_skipped += 1
                continue

            labels_to_update.append(r)

        result.total_processed = len(labels_to_update)

        if dry_run:
            logger.info("[DRY RUN] Would update %d labels", len(labels_to_update))

            # Count by type of change (count all, not just displayed ones)
            label_type_changes = 0
            consensus_updates = 0
            other_changes = 0

            for r in labels_to_update:
                if r.get("suggested_label_type") and r["suggested_label_type"] != r["label_type"]:
                    label_type_changes += 1
                elif r.get("consensus_label") and r.get("current_label") != r["consensus_label"]:
                    consensus_updates += 1
                else:
                    other_changes += 1

            # Show first 20 examples
            shown = 0
            for r in labels_to_update:
                if shown >= 20:
                    break

                if r.get("suggested_label_type") and r["suggested_label_type"] != r["label_type"]:
                    # Label type change (outlier correction)
                    logger.info(
                        "[LABEL TYPE CHANGE] %s...#%s#%s#%s: Current: %s (%s) → "
                        "New: %s (PENDING) | Old label would be marked: INVALID | "
                        "Audit trail: label_consolidated_from=%s",
                        r['image_id'][:8],
                        r['receipt_id'],
                        r['line_id'],
                        r['word_id'],
                        r['current_label'],
                        r['label_type'],
                        r['suggested_label_type'],
                        r['current_label'],
                    )
                    shown += 1
                elif r.get("consensus_label") and r.get("current_label") != r["consensus_label"]:
                    # Consensus update (same label type, different value)
                    logger.info(
                        "[CONSENSUS UPDATE] %s...#%s#%s#%s: Current: %s → "
                        "New: %s (VALID) | Old label would be marked: SUPERSEDED | "
                        "Audit trail: label_consolidated_from=%s",
                        r['image_id'][:8],
                        r['receipt_id'],
                        r['line_id'],
                        r['word_id'],
                        r['current_label'],
                        r['consensus_label'],
                        r['current_label'],
                    )
                    shown += 1
                else:
                    # Other changes
                    logger.info(
                        "%s...#%s#%s#%s: %s",
                        r['image_id'][:8],
                        r['receipt_id'],
                        r['line_id'],
                        r['word_id'],
                        r['changes_needed'],
                    )
                    shown += 1

            if len(labels_to_update) > 20:
                remaining = len(labels_to_update) - 20
                logger.info("  ... and %d more labels", remaining)

            # Summary by change type
            logger.info(
                "[DRY RUN SUMMARY] Label type changes: %d, Consensus updates: %d, "
                "Other changes: %d, Total: %d",
                label_type_changes,
                consensus_updates,
                other_changes,
                len(labels_to_update),
            )

            result.total_updated = len(labels_to_update)
            return result

        # Actually apply updates
        logger.info(f"Applying updates to {len(labels_to_update)} labels...")

        for r in labels_to_update:
            try:
                # Get the current label record
                label = self.dynamo.get_receipt_word_label(
                    r["image_id"],
                    r["receipt_id"],
                    r["line_id"],
                    r["word_id"],
                    r["current_label"],
                )

                if not label:
                    logger.warning(
                        f"Label not found: {r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}#{r['current_label']}"
                    )
                    result.total_failed += 1
                    result.errors.append(
                        f"{r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: not found"
                    )
                    continue

                # Handle label type change (outlier with suggested_label_type)
                if r.get("suggested_label_type") and r["suggested_label_type"] != r["label_type"]:
                    # This is an outlier that should be moved to a different label type
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    # First, check if the word already has the suggested label type
                    existing_labels_result = self.dynamo.list_receipt_word_labels_for_word(
                        image_id=r["image_id"],
                        receipt_id=r["receipt_id"],
                        line_id=r["line_id"],
                        word_id=r["word_id"],
                    )
                    existing_labels = existing_labels_result[0]  # Get the list from tuple

                    # Check if suggested label type already exists (and is VALID or PENDING)
                    existing_suggested_label = None
                    for existing_label in existing_labels:
                        if existing_label.label == r["suggested_label_type"]:
                            if existing_label.validation_status in ["VALID", "PENDING"]:
                                existing_suggested_label = existing_label
                                break

                    if existing_suggested_label:
                        # Word already has the correct label type - just mark the incorrect one as INVALID
                        logger.info(
                            "Word already has correct label type %s (%s). "
                            "Marking incorrect %s label as INVALID.",
                            r['suggested_label_type'],
                            existing_suggested_label.validation_status,
                            r['label_type'],
                        )

                        if dry_run:
                            logger.info(
                                "[DRY RUN] Would mark %s label as INVALID for "
                                "%s...#%s#%s#%s "
                                "(word already has correct %s label)",
                                r['label_type'],
                                r['image_id'][:8],
                                r['receipt_id'],
                                r['line_id'],
                                r['word_id'],
                                r['suggested_label_type'],
                            )
                            result.total_updated += 1
                        else:
                            # Mark old label as INVALID (word already has the correct label)
                            label.validation_status = ValidationStatus.INVALID.value
                            self.dynamo.update_receipt_word_label(label)
                            logger.info(
                                "Marked %s label as INVALID for "
                                "%s...#%s#%s#%s "
                                "(word already has correct %s label)",
                                r['label_type'],
                                r['image_id'][:8],
                                r['receipt_id'],
                                r['line_id'],
                                r['word_id'],
                                r['suggested_label_type'],
                            )
                            result.total_updated += 1
                    else:
                        # Word doesn't have the suggested label type - create new label
                        if dry_run:
                            # Dry run: just log what would happen
                            logger.info(
                                "[DRY RUN] Would update label type %s...#%s#%s#%s: "
                                "%s → %s (old label would be marked INVALID, new label would be PENDING)",
                                r['image_id'][:8],
                                r['receipt_id'],
                                r['line_id'],
                                r['word_id'],
                                r['label_type'],
                                r['suggested_label_type'],
                            )
                            result.total_updated += 1
                        else:
                            # Update old label's validation_status to INVALID (it was incorrectly labeled)
                            label.validation_status = ValidationStatus.INVALID.value
                            self.dynamo.update_receipt_word_label(label)

                            # Create new label with suggested label type
                            # Use label_consolidated_from to track the previous (incorrect) label for audit trail
                            new_label = ReceiptWordLabel(
                                image_id=label.image_id,
                                receipt_id=label.receipt_id,
                                line_id=label.line_id,
                                word_id=label.word_id,
                                label=r["suggested_label_type"],  # New label type
                                reasoning=f"Label type corrected from {r['label_type']} (outlier) to {r['suggested_label_type']}. Previous label marked as INVALID.",
                                timestamp_added=datetime.now().isoformat(),
                                validation_status=ValidationStatus.PENDING.value,  # Mark as PENDING for review since it's a new label type
                                label_proposed_by="label-harmonizer",
                                label_consolidated_from=label.label,  # Audit trail: tracks the previous (incorrect) label
                            )
                            self.dynamo.add_receipt_word_label(new_label)

                            logger.info(
                                "Updated label type %s...#%s#%s#%s: "
                                "%s → %s (old label marked INVALID, new label PENDING)",
                                r['image_id'][:8],
                                r['receipt_id'],
                                r['line_id'],
                                r['word_id'],
                                r['label_type'],
                                r['suggested_label_type'],
                            )
                            result.total_updated += 1

                # Update label if consensus is different (same label type)
                elif r["consensus_label"] and label.label != r["consensus_label"]:
                    # Note: In DynamoDB, labels are keyed by (image_id, receipt_id, line_id, word_id, label)
                    # Create new label with consensus value and mark old one as SUPERSEDED for audit trail
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    # Update old label's validation_status to SUPERSEDED (replaced by consensus, preserves audit trail)
                    label.validation_status = ValidationStatus.SUPERSEDED.value
                    self.dynamo.update_receipt_word_label(label)

                    # Create new label with consensus value
                    # Use label_consolidated_from to track the previous label for audit trail
                    new_label = ReceiptWordLabel(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=r["consensus_label"],
                        reasoning=f"Updated to consensus: {label.reasoning or 'N/A'}. Previous label marked as SUPERSEDED.",
                        timestamp_added=datetime.now().isoformat(),
                        validation_status=ValidationStatus.VALID.value,
                        label_proposed_by=label.label_proposed_by or "label-harmonizer",
                        label_consolidated_from=label.label,  # Audit trail: tracks the previous label that was superseded
                    )
                    self.dynamo.add_receipt_word_label(new_label)

                    logger.info(
                        f"Updated {r['image_id'][:8]}...#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: "
                        f"{label.label} → {r['consensus_label']} (old label marked SUPERSEDED)"
                    )
                    result.total_updated += 1

            except Exception as e:
                logger.error(
                    f"Failed to update {r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: {e}"
                )
                result.total_failed += 1
                result.errors.append(f"{r['image_id']}#{r['receipt_id']}#{r['line_id']}#{r['word_id']}: {e}")

        logger.info(
            f"Update complete: {result.total_updated} updated, "
            f"{result.total_failed} failed, {result.total_skipped} skipped"
        )

        return result

    def print_summary(self, report: dict[str, Any]) -> None:
        """Print a human-readable summary of the harmonization report."""
        stats = report["stats"]
        summaries = report["summary"]

        print("=" * 70)
        print(f"LABEL HARMONIZER REPORT ({stats['label_type']})")
        print("=" * 70)
        print(f"Total labels: {stats['total_labels']}")
        print(f"  With merchant: {stats['labels_with_merchant']}")
        print(f"  Without merchant: {stats['labels_without_merchant']} (cannot harmonize)")
        print(f"Merchant groups: {stats['merchant_groups']}")
        print()

        # Overall consistency
        total_with_merchant = stats['labels_with_merchant']
        if total_with_merchant > 0:
            consistent = sum(s["consistent"] for s in summaries)
            needs_update = sum(s["needs_update"] for s in summaries)
            print(f"Consistency (labels with merchant):")
            print(f"  ✅ Consistent: {consistent} ({consistent/total_with_merchant*100:.1f}%)")
            print(f"  ⚠️  Needs update: {needs_update} ({needs_update/total_with_merchant*100:.1f}%)")
        print()

        # Groups with issues
        print("Merchants needing attention (sorted by consistency):")
        shown = 0
        for s in summaries:
            if s["needs_update"] > 0 and shown < 20:
                status = "✅" if s["pct_consistent"] >= 80 else "⚠️"
                print(
                    f"  {status} {s['merchant_name']}: "
                    f"{s['consistent']}/{s['total_labels']} consistent "
                    f"({s['pct_consistent']:.0f}%) - consensus: {s['consensus_label']}"
                )
                shown += 1

        if len([s for s in summaries if s["needs_update"] > 0]) > 20:
            remaining = len([s for s in summaries if s["needs_update"] > 0]) - 20
            print(f"  ... and {remaining} more merchants")

        # Fully consistent groups
        fully_consistent = [s for s in summaries if s["pct_consistent"] == 100]
        print()
        print(f"Fully consistent merchants: {len(fully_consistent)}")
        for s in fully_consistent[:10]:
            print(f"  ✅ {s['merchant_name']}: {s['total_labels']} labels")

