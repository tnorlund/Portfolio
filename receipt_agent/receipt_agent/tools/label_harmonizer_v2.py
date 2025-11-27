"""
Label Harmonizer V2 - Proper LangSmith Trace Nesting
=====================================================

This is a refactored version of LabelHarmonizer with proper LangSmith trace nesting.
All async calls within a harmonization run appear under a single trace.

Key Changes from V1:
-------------------
1. Uses `async with ls.trace(...)` for root trace context
2. Passes `run_tree` explicitly via `langsmith_extra={"parent": run_tree}`
3. All `@traceable` functions properly nest under their parent trace

This follows the LangSmith documentation for trace nesting in Python < 3.11:
https://docs.langchain.com/langsmith/nest-traces

Usage:
------
```python
from receipt_agent.tools.label_harmonizer_v2 import LabelHarmonizerV2

harmonizer = LabelHarmonizerV2(dynamo_client, chroma_client, embed_fn)
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
import random
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List, Tuple

from langchain_ollama import ChatOllama
from receipt_agent.config.settings import Settings, get_settings

try:
    import langsmith as ls
    from langsmith import traceable
    HAS_LANGSMITH = True
except ImportError:
    # LangSmith not available - traceable will be a no-op
    ls = None  # type: ignore
    HAS_LANGSMITH = False
    def traceable(func):
        return func

try:
    from receipt_label.constants import CORE_LABELS
except ImportError:
    # Fallback if receipt_label is not available
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


def _build_chromadb_word_id(image_id: str, receipt_id: int, line_id: int, word_id: int) -> str:
    """Build ChromaDB document ID for a word."""
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"


@dataclass
class LabelRecord:
    """A single label record."""
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    validation_status: Optional[str] = None
    merchant_name: Optional[str] = None
    word_text: Optional[str] = None
    is_noise: bool = False  # Noise words are not embedded in ChromaDB


@dataclass
class MerchantLabelGroup:
    """A group of labels sharing the same merchant_name and label type."""
    merchant_name: str
    label_type: str
    labels: list[LabelRecord] = field(default_factory=list)
    consensus_label: Optional[str] = None
    similarity_clusters: list[list[LabelRecord]] = field(default_factory=list)
    outliers: list[LabelRecord] = field(default_factory=list)


@dataclass
class HarmonizerResult:
    """Result of harmonization for a single label."""
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    merchant_name: Optional[str]
    label_type: str
    current_label: str
    consensus_label: Optional[str]
    suggested_label_type: Optional[str] = None
    needs_update: bool = False
    changes_needed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    group_size: int = 1
    cluster_size: int = 1


@dataclass
class UpdateResult:
    """Result of applying fixes to DynamoDB."""
    total_processed: int = 0
    total_updated: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    total_needs_review: int = 0  # Outliers marked as NEEDS_REVIEW (no validated suggestion)
    errors: list[str] = field(default_factory=list)


class LabelHarmonizerV2:
    """
    Harmonizes receipt word labels with proper LangSmith trace nesting.

    All LLM calls and async operations appear under a single trace in LangSmith.
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
            embed_fn: Function to generate embeddings
            llm: Optional LLM client for outlier detection
            settings: Optional settings
        """
        self.dynamo = dynamo_client
        self.chroma = chroma_client
        self.embed_fn = embed_fn

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
        """Stream labels for a specific label type from DynamoDB."""
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
        """Get merchant_name for a batch of labels."""
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
        """Get word data (text and is_noise) for a batch of labels."""
        word_keys = set((l.image_id, l.receipt_id, l.line_id, l.word_id) for l in labels)
        words_by_key = {}

        for image_id, receipt_id, line_id, word_id in word_keys:
            try:
                word = self.dynamo.get_receipt_word(receipt_id, image_id, line_id, word_id)
                if word:
                    words_by_key[(image_id, receipt_id, line_id, word_id)] = {
                        "text": word.text,
                        "is_noise": word.is_noise,
                    }
            except Exception as e:
                logger.debug(f"Could not get word {image_id}#{receipt_id}#{line_id}#{word_id}: {e}")

        return words_by_key

    async def _validate_suggestion_with_similarity(
        self,
        word: LabelRecord,
        suggested_label_type: str,
        merchant_name: str,
        llm_reason: str,
    ) -> Tuple[bool, Optional[List[dict]]]:
        """
        Validate a suggested label type by querying ChromaDB for similar embeddings
        and having an LLM decide if the suggestion makes sense.

        This prevents garbage suggestions like "Vons." -> WEBSITE by:
        1. Finding semantically similar words that have the suggested label type
        2. Asking the LLM if these similar words support the suggestion

        Args:
            word: The LabelRecord being classified (has image_id, receipt_id, etc.)
            suggested_label_type: The label type the LLM suggested
            merchant_name: The merchant name for context
            llm_reason: The reasoning from the initial suggestion LLM

        Returns:
            Tuple of (is_validated, similar_words_context)
            - is_validated: True if LLM validates the suggestion
            - similar_words_context: List of similar word info, or None
        """
        if not self.chroma or not self.embed_fn:
            logger.debug("ChromaDB not available for similarity validation")
            return False, None

        if not word.word_text:
            logger.debug("Word text empty, cannot validate")
            return False, None

        try:
            # Noise words are not embedded in ChromaDB - skip validation
            if word.is_noise:
                logger.debug(
                    "Word '%s' is noise, skipping similarity validation",
                    word.word_text
                )
                return False, None

            # Build the ChromaDB document ID for this word
            # Same pattern as _find_similar_words()
            chroma_id = _build_chromadb_word_id(
                word.image_id, word.receipt_id, word.line_id, word.word_id
            )

            # Get the embedding for our target word from ChromaDB
            # Same pattern as _find_similar_words()
            results = self.chroma.get(
                collection_name="words",
                ids=[chroma_id],
                include=["embeddings", "metadatas"],
            )

            if not results or not results.get("ids") or not results["ids"]:
                logger.warning(
                    "Word '%s' (id=%s) not found in ChromaDB",
                    word.word_text, chroma_id
                )
                return False, None

            embeddings = results.get("embeddings")
            # Check if embeddings exist - be careful with numpy arrays
            if embeddings is None or len(embeddings) == 0 or embeddings[0] is None:
                logger.warning(
                    "No embedding for word '%s' (id=%s) in ChromaDB",
                    word.word_text, chroma_id
                )
                return False, None

            target_embedding = embeddings[0]
            # Convert to list if numpy array (ChromaDB may return numpy arrays)
            if hasattr(target_embedding, 'tolist'):
                target_embedding = target_embedding.tolist()

            # Query for similar words (same pattern as _identify_outliers)
            # Filter by merchant_name if available, then filter in Python for validated_labels
            merchant_filter = merchant_name.strip().title() if merchant_name else None
            where_clause = {"merchant_name": {"$eq": merchant_filter}} if merchant_filter else None

            query_results = self.chroma.query(
                collection_name="words",
                query_embeddings=[target_embedding],
                n_results=50,  # Get more results to filter in Python
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )

            if not query_results or not query_results.get("documents") or not query_results["documents"][0]:
                logger.info(
                    "No similar words found for '%s'",
                    word.word_text
                )
                return False, None

            # Filter results to only include words with the suggested label type in validated_labels
            # validated_labels is stored as comma-delimited: ",LABEL1,LABEL2,"
            # Same pattern as _identify_outliers (line 904-906)
            label_pattern = f",{suggested_label_type},"
            filtered_docs = []
            filtered_metadatas = []
            filtered_distances = []

            for doc, metadata, distance in zip(
                query_results["documents"][0],
                query_results["metadatas"][0] if query_results.get("metadatas") else [{}] * len(query_results["documents"][0]),
                query_results["distances"][0] if query_results.get("distances") else [1.0] * len(query_results["documents"][0])
            ):
                validated_labels_str = metadata.get("validated_labels", "")
                if label_pattern in validated_labels_str:
                    filtered_docs.append(doc)
                    filtered_metadatas.append(metadata)
                    filtered_distances.append(distance)
                    # Stop at 10 results after filtering
                    if len(filtered_docs) >= 10:
                        break

            if not filtered_docs:
                logger.info(
                    "No similar words with validated label '%s' found for '%s'",
                    suggested_label_type, word.word_text
                )
                return False, None

            # Build context about similar words for LLM validation
            similar_words_context = []
            for i, (doc, metadata, distance) in enumerate(zip(
                filtered_docs,
                filtered_metadatas,
                filtered_distances
            )):
                similarity = 1 - (distance / 2)  # Convert distance to similarity (0-1)
                similar_words_context.append({
                    "text": doc,
                    "similarity": round(similarity, 3),
                    "merchant": metadata.get("merchant_name", "Unknown"),
                    "left_context": metadata.get("left", ""),
                    "right_context": metadata.get("right", ""),
                    # We queried for this specific label type, so use it
                    "label": suggested_label_type,
                    "validated_labels": metadata.get("validated_labels", ""),
                    # Include IDs for fetching full receipt context
                    "image_id": metadata.get("image_id"),
                    "receipt_id": metadata.get("receipt_id"),
                    "line_id": metadata.get("line_id"),
                    "word_id": metadata.get("word_id"),
                })

            # Now ask the LLM to validate based on the similar words
            is_valid = await self._llm_validate_suggestion(
                word_text=word.word_text,
                suggested_label_type=suggested_label_type,
                merchant_name=merchant_name,
                llm_reason=llm_reason,
                similar_words=similar_words_context,
            )

            return is_valid, similar_words_context if is_valid else None

        except Exception as e:
            logger.warning("Similarity validation error for '%s': %s", word.word_text, e)
            return False, None

    async def _enrich_similar_words_with_context(
        self,
        similar_words: List[dict],
    ) -> List[dict]:
        """
        Enrich similar words with full receipt context (surrounding lines).

        For each similar word, fetches:
        - The line the word is on
        - Surrounding lines from the receipt
        - Marks the target word in context
        """
        enriched = []

        for w in similar_words:
            enriched_word = w.copy()

            # Try to fetch full context if we have the IDs
            if self.dynamo and w.get('image_id') and w.get('receipt_id') and w.get('line_id'):
                try:
                    # Get the line this word is on
                    line = self.dynamo.get_receipt_line(
                        receipt_id=w['receipt_id'],
                        image_id=w['image_id'],
                        line_id=w['line_id']
                    )
                    if line:
                        enriched_word['line_context'] = line.text

                    # Get surrounding lines for full receipt context
                    all_lines = self.dynamo.list_receipt_lines_from_receipt(
                        image_id=w['image_id'],
                        receipt_id=w['receipt_id']
                    )

                    if all_lines:
                        # Sort by y position
                        sorted_lines = sorted(all_lines, key=lambda l: l.calculate_centroid()[1])

                        # Find target line index
                        target_idx = None
                        for i, receipt_line in enumerate(sorted_lines):
                            if receipt_line.line_id == w.get('line_id'):
                                target_idx = i
                                break

                        if target_idx is not None:
                            # Get 3 lines before and after
                            start_idx = max(0, target_idx - 3)
                            end_idx = min(len(sorted_lines), target_idx + 4)

                            context_lines = []
                            for i in range(start_idx, end_idx):
                                line_text = sorted_lines[i].text
                                if i == target_idx:
                                    # Mark the target line
                                    context_lines.append(f">>> {line_text} <<<")
                                else:
                                    context_lines.append(f"    {line_text}")

                            enriched_word['surrounding_lines'] = context_lines

                except Exception as e:
                    logger.debug("Could not fetch context for similar word: %s", e)

            enriched.append(enriched_word)

        return enriched

    @traceable
    async def _llm_validate_suggestion(
        self,
        word_text: str,
        suggested_label_type: str,
        merchant_name: str,
        llm_reason: str,
        similar_words: List[dict],
    ) -> bool:
        """
        Use LLM to validate if a suggested label type makes sense based on similar words.

        The LLM sees:
        1. The target word and context
        2. The suggested label type and reasoning
        3. Similar words that have that label type WITH full receipt context

        And decides if the suggestion is valid.
        """
        if not self.llm:
            logger.warning("LLM not available for suggestion validation")
            return False

        # Enrich similar words with ±3 lines context (same as original prompt)
        enriched_similar_words = await self._enrich_similar_words_with_context(similar_words[:5])

        # Format similar words concisely - just show context like original prompt
        similar_words_text = ""
        for i, w in enumerate(enriched_similar_words, 1):
            similar_words_text += f"\n{i}. **\"{w['text']}\"** ({w['similarity']:.0%} similar, {w['merchant']})\n"
            if w.get('surrounding_lines'):
                similar_words_text += "   ```\n"
                similar_words_text += "\n".join(f"   {line}" for line in w['surrounding_lines'])
                similar_words_text += "\n   ```\n"
            elif w.get('line_context'):
                similar_words_text += f"   Line: `{w['line_context']}`\n"
            else:
                similar_words_text += f"   Context: {w.get('left_context', '')} [{w['text']}] {w.get('right_context', '')}\n"

        prompt = f"""# Validate Label Type Suggestion

**Word:** `"{word_text}"` | **Merchant:** {merchant_name} | **Suggested Label:** `{suggested_label_type}`

**Your reasoning:** {llm_reason or "No reasoning provided"}

## Similar Words with `{suggested_label_type}` Label (±3 lines context)
{similar_words_text if enriched_similar_words else "NO SIMILAR WORDS FOUND with this label type."}

## Question

Does `"{word_text}"` appear in a similar context to these examples? If no similar words were found, is this suggestion likely wrong?

Respond with JSON: `is_valid` (boolean), `reasoning` (string)"""

        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.runnables import RunnableConfig
            from pydantic import BaseModel, Field

            class ValidationResult(BaseModel):
                is_valid: bool = Field(description="True if suggestion is valid")
                reasoning: str = Field(description="Explanation for the decision")

            messages = [HumanMessage(content=prompt)]

            config = RunnableConfig(
                run_name="validate_label_suggestion",
                metadata={
                    "task": "validate_suggestion",
                    "word": word_text,
                    "suggested_label_type": suggested_label_type,
                    "merchant": merchant_name,
                    "similar_words_count": len(similar_words),
                },
                tags=["label-harmonizer-v2", "validation", "similarity-check"],
            )

            json_schema = ValidationResult.model_json_schema()
            llm_with_schema = ChatOllama(
                model=self.llm.model,
                base_url=self.llm.base_url,
                client_kwargs=self.llm.client_kwargs,
                format=json_schema,
                temperature=0.0,
            )
            llm_structured = llm_with_schema.with_structured_output(ValidationResult)

            response = await llm_structured.ainvoke(messages, config=config)
            is_valid = getattr(response, 'is_valid', False)
            reasoning = getattr(response, 'reasoning', "")

            logger.info(
                "LLM validation for '%s' -> %s: %s (reason: %s)",
                word_text, suggested_label_type,
                "VALID" if is_valid else "REJECTED",
                reasoning[:100] if reasoning else "N/A"
            )

            return is_valid

        except Exception as e:
            logger.warning("LLM validation failed for '%s': %s", word_text, e)
            return False

    def load_labels_by_type(
        self,
        label_type: str,
        batch_size: int = 1000,
        max_merchants: Optional[int] = None,
    ) -> int:
        """Load labels for a specific label type."""
        logger.info("Loading labels: label_type=%s", label_type)

        self._merchant_groups = {}
        self._no_merchant_labels = []
        self._label_type = label_type
        total = 0
        merchants_processed = 0

        for batch_num, label_batch in enumerate(self._stream_labels_by_type(label_type)):
            metadata_by_key = self._get_merchant_for_labels(label_batch)
            words_by_key = self._get_words_for_labels(label_batch)

            for label in label_batch:
                key = (label.image_id, label.receipt_id)
                merchant_name = metadata_by_key.get(key)
                word_key = (label.image_id, label.receipt_id, label.line_id, label.word_id)
                word_data = words_by_key.get(word_key, {})
                word_text = word_data.get("text") if isinstance(word_data, dict) else word_data
                is_noise = word_data.get("is_noise", False) if isinstance(word_data, dict) else False

                record = LabelRecord(
                    image_id=label.image_id,
                    receipt_id=label.receipt_id,
                    line_id=label.line_id,
                    word_id=label.word_id,
                    label=label.label,
                    validation_status=label.validation_status,
                    merchant_name=merchant_name,
                    word_text=word_text,
                    is_noise=is_noise,
                )

                if merchant_name:
                    group_key = (merchant_name, label_type)
                    if group_key not in self._merchant_groups:
                        if max_merchants and merchants_processed >= max_merchants:
                            continue
                        self._merchant_groups[group_key] = MerchantLabelGroup(
                            merchant_name=merchant_name,
                            label_type=label_type,
                        )
                        merchants_processed += 1
                    self._merchant_groups[group_key].labels.append(record)
                else:
                    self._no_merchant_labels.append(record)

                total += 1

        logger.info(
            "Loaded labels: total=%d merchants=%d",
            total,
            len(self._merchant_groups),
        )
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

        from collections import defaultdict
        valid_labels: Dict[str, int] = defaultdict(int)
        all_labels: Dict[str, int] = defaultdict(int)

        # Also group by word text to validate that similar words have same label/validation_status
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

        # Check for word text inconsistencies
        word_text_conflicts = []
        for word_text, label_counts in labels_by_word_text.items():
            if len(label_counts) > 1:
                word_text_conflicts.append({
                    "word_text": word_text,
                    "conflict_type": "different_labels",
                    "labels": dict(label_counts),
                })

        for word_text, status_counts in validation_status_by_word_text.items():
            if len(status_counts) > 1:
                word_text_conflicts.append({
                    "word_text": word_text,
                    "conflict_type": "different_validation_statuses",
                    "validation_statuses": dict(status_counts),
                })

        if word_text_conflicts:
            logger.warning(
                f"Found {len(word_text_conflicts)} word text conflicts for "
                f"{group.merchant_name} {group.label_type}: {word_text_conflicts[:3]}"
            )

        # Prefer VALID labels if available
        if valid_labels:
            group.consensus_label = max(valid_labels.items(), key=lambda x: x[1])[0]
        elif all_labels:
            group.consensus_label = max(all_labels.items(), key=lambda x: x[1])[0]

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
        """Find similar words using ChromaDB."""
        if not self.chroma or not self.embed_fn or not group.labels:
            return

        chroma_ids = []
        label_records = []
        for record in group.labels:
            chroma_id = _build_chromadb_word_id(
                record.image_id, record.receipt_id, record.line_id, record.word_id
            )
            chroma_ids.append(chroma_id)
            label_records.append((chroma_id, record))

        try:
            results = self.chroma.get(
                collection_name="words",
                ids=chroma_ids,
                include=["embeddings", "metadatas"],
            )

            if not results or not results.get("ids"):
                return

            found_ids = results.get("ids", [])
            embeddings = results.get("embeddings", [])

            embeddings_by_id = {}
            for i, chroma_id in enumerate(found_ids):
                if i < len(embeddings) and embeddings[i] is not None:
                    embeddings_by_id[chroma_id] = embeddings[i]

            clusters = []
            processed = set()

            for chroma_id, record in label_records:
                if chroma_id in processed or chroma_id not in embeddings_by_id:
                    continue

                embedding = embeddings_by_id[chroma_id]
                cluster = [record]
                processed.add(chroma_id)

                for other_id, other_record in label_records:
                    if other_id in processed or other_id not in embeddings_by_id:
                        continue
                    other_embedding = embeddings_by_id[other_id]
                    similarity = self._cosine_similarity(embedding, other_embedding)
                    if similarity >= similarity_threshold:
                        cluster.append(other_record)
                        processed.add(other_id)

                if len(cluster) > 1:
                    clusters.append(cluster)

            group.similarity_clusters = clusters

        except Exception as e:
            logger.error(f"Error finding similar words: {e}")

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
        run_tree: Optional[Any] = None,
        max_concurrent_llm_calls: int = 2,
    ) -> None:
        """
        Identify outliers in the group using concurrent LLM calls.

        Args:
            group: The merchant label group
            similarity_threshold: Minimum similarity for matches
            run_tree: Parent run tree for LangSmith trace nesting
            max_concurrent_llm_calls: Max concurrent LLM calls (default: 5)
        """
        if not group.labels or len(group.labels) < 3:
            return

        if not self.chroma or not self.embed_fn:
            return

        logger.debug("Identifying outliers for %s %s", group.merchant_name, group.label_type)

        chroma_ids = []
        label_records = []
        for record in group.labels:
            chroma_id = _build_chromadb_word_id(
                record.image_id, record.receipt_id, record.line_id, record.word_id
            )
            chroma_ids.append(chroma_id)
            label_records.append((chroma_id, record))

        try:
            results = self.chroma.get(
                collection_name="words",
                ids=chroma_ids,
                include=["embeddings", "metadatas"],
            )

            if not results or not results.get("ids"):
                return

            found_ids = results.get("ids", [])
            embeddings = results.get("embeddings", [])

            embeddings_by_id = {}
            for i, chroma_id in enumerate(found_ids):
                if i < len(embeddings) and embeddings[i] is not None:
                    embeddings_by_id[chroma_id] = embeddings[i]

            merchant_filter = group.merchant_name.strip().title() if group.merchant_name else None

            # Sort records to prioritize checking INVALID and NEEDS_REVIEW first
            sorted_records = sorted(
                label_records,
                key=lambda x: (
                    0 if x[1].validation_status == "INVALID" else
                    1 if x[1].validation_status == "NEEDS_REVIEW" else 2
                )
            )

            # Limit LLM checks to fit within Lambda timeout
            # With 5 concurrent calls and ~5s per call, 50 checks = ~50s
            max_llm_checks = min(50, len(group.labels))

            # Phase 1: Gather all work items that need LLM checks
            work_items = []
            for chroma_id, record in sorted_records:
                if len(work_items) >= max_llm_checks:
                    break

                if chroma_id not in embeddings_by_id:
                    continue

                if record.validation_status == "VALID":
                    continue

                embedding = embeddings_by_id[chroma_id]

                try:
                    where_clause = {"merchant_name": {"$eq": merchant_filter}} if merchant_filter else None

                    query_results = self.chroma.query(
                        collection_name="words",
                        query_embeddings=[embedding],
                        n_results=50,
                        where=where_clause,
                        include=["metadatas", "distances"],
                    )

                    similar_ids = query_results.get("ids", [[]])[0] if query_results else []
                    distances = query_results.get("distances", [[]])[0] if query_results else []
                    metadatas = query_results.get("metadatas", [[]])[0] if query_results else []

                    similar_matches = []
                    label_pattern = f",{group.label_type},"

                    for idx, similar_id in enumerate(similar_ids):
                        if similar_id == chroma_id:
                            continue
                        if idx >= len(distances) or idx >= len(metadatas):
                            continue

                        similarity = max(0.0, 1.0 - (distances[idx] / 2))
                        if similarity < similarity_threshold:
                            continue

                        metadata = metadatas[idx] if metadatas else {}
                        validated_labels_str = metadata.get("validated_labels", "")

                        if label_pattern in validated_labels_str:
                            similar_matches.append((similar_id, similarity))

                    work_items.append({
                        "record": record,
                        "similar_matches": similar_matches,
                        "metadatas": metadatas,
                        "similar_ids": similar_ids,
                    })

                except Exception as e:
                    logger.debug("ChromaDB query error: word='%s' error=%s", record.word_text, e)
                    continue

            logger.debug(
                "Prepared %d LLM checks for %s (max_concurrent=%d)",
                len(work_items),
                group.merchant_name,
                max_concurrent_llm_calls,
            )

            # Phase 2: Process LLM calls concurrently with semaphore
            semaphore = asyncio.Semaphore(max_concurrent_llm_calls)
            outliers = []
            outliers_lock = asyncio.Lock()

            async def check_outlier(item: dict) -> None:
                """Check a single word for outlier status with concurrency control."""
                async with semaphore:
                    try:
                        if HAS_LANGSMITH and run_tree:
                            is_outlier = await self._llm_determine_outlier(
                                word=item["record"],
                                similar_matches=item["similar_matches"],
                                group=group,
                                metadatas=item["metadatas"],
                                similar_ids=item["similar_ids"],
                                langsmith_extra={"parent": run_tree},  # type: ignore[call-arg]
                            )
                        else:
                            is_outlier = await self._llm_determine_outlier(
                                word=item["record"],
                                similar_matches=item["similar_matches"],
                                group=group,
                                metadatas=item["metadatas"],
                                similar_ids=item["similar_ids"],
                            )

                        if is_outlier:
                            async with outliers_lock:
                                outliers.append(item["record"])
                            logger.debug(
                                "Outlier detected: word='%s' label_type='%s'",
                                item['record'].word_text,
                                group.label_type,
                            )
                    except Exception as e:
                        logger.error(f"Failed outlier detection for '{item['record'].word_text}': {e}")

            # Run all LLM checks concurrently (limited by semaphore)
            await asyncio.gather(*[check_outlier(item) for item in work_items])

            group.outliers = outliers
            if outliers:
                logger.info(
                    "Outliers: merchant=%s label_type=%s count=%d",
                    group.merchant_name,
                    group.label_type,
                    len(outliers),
                )

        except Exception as e:
            logger.error(f"Error identifying outliers: {e}")

    @traceable
    async def _llm_determine_outlier(
        self,
        word: LabelRecord,
        similar_matches: List[Tuple[str, float]],
        group: MerchantLabelGroup,
        metadatas: List[Dict],
        similar_ids: List[str],
    ) -> bool:
        """
        Use LLM to determine if a word is an outlier.

        This function is decorated with @traceable. When called with
        langsmith_extra={"parent": run_tree}, it will properly nest
        under the parent trace.
        """
        if not self.llm:
            raise ValueError("LLM not available for outlier detection")

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
                        surrounding_words = word_texts

                # Get all lines from receipt formatted spatially
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
            logger.debug(f"Could not fetch line context: {e}")

        # Build context about similar words, including their line context
        similar_words_info = []
        for similar_id, similarity in similar_matches[:10]:
            similar_idx = similar_ids.index(similar_id) if similar_id in similar_ids else -1
            if similar_idx >= 0 and similar_idx < len(metadatas):
                metadata = metadatas[similar_idx]

                # Fetch line context for this similar word
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
                                target_idx = similar_word_index - start_idx
                                if similar_surrounding_words and target_idx < len(similar_surrounding_words):
                                    similar_surrounding_words[target_idx] = f"[{similar_surrounding_words[target_idx]}]"
                            except (StopIteration, ValueError):
                                similar_surrounding_words = [w.text for w in similar_words_in_line]

                        # Get all lines from similar receipt
                        all_similar_receipt_lines = self.dynamo.list_receipt_lines_from_receipt(
                            image_id=similar_image_id,
                            receipt_id=similar_receipt_id
                        )
                        if all_similar_receipt_lines and similar_line:
                            sorted_similar_lines = sorted(all_similar_receipt_lines, key=lambda l: l.calculate_centroid()[1])
                            formatted_lines = []
                            for i, receipt_line in enumerate(sorted_similar_lines):
                                if i > 0:
                                    prev_line = sorted_similar_lines[i - 1]
                                    curr_centroid = receipt_line.calculate_centroid()
                                    if prev_line.bottom_left["y"] < curr_centroid[1] < prev_line.top_left["y"]:
                                        if receipt_line.line_id == similar_line_id:
                                            formatted_lines[-1] += f" [{receipt_line.text}]"
                                        else:
                                            formatted_lines[-1] += f" {receipt_line.text}"
                                        continue
                                if receipt_line.line_id == similar_line_id:
                                    formatted_lines.append(f"[{receipt_line.text}]")
                                else:
                                    formatted_lines.append(receipt_line.text)
                            similar_surrounding_lines = formatted_lines
                except Exception as e:
                    logger.debug(f"Could not fetch line context for similar word {similar_id}: {e}")

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
            for part in group.merchant_name.replace("-", " ").replace("&", " ").split():
                cleaned = part.strip().upper()
                if cleaned:
                    merchant_words.add(cleaned)

        label_definition = CORE_LABELS.get(group.label_type, "N/A")

        # Build prompt for LLM with markdown-friendly formatting
        context_section = f"""## Context

- **Merchant:** {group.merchant_name}
- **Label Type:** `{group.label_type}`
- **Label Type Definition:** {label_definition}
- **Word being evaluated:** `"{word.word_text}"`
- **Current validation status:** {word.validation_status or 'PENDING'}"""

        # Add merchant name components as a hint for MERCHANT_NAME label type
        if merchant_words and group.label_type == "MERCHANT_NAME":
            context_section += f"\n- **Note:** The merchant name contains these components: {sorted(merchant_words)}. These may be valid when appearing in merchant name context (header/store info area), but **context is the primary factor**."

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
                    prompt += "- **Surrounding lines** (spatial order, target line marked):\n  ```\n  " + "\n  ".join(info['surrounding_lines']) + "\n  ```\n"
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

5. **OCR/scanning errors:** Receipt text may have word boundary issues, missing characters, or character substitutions."""

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
        # Uses exponential backoff with jitter to prevent thundering herd
        max_retries = 3
        base_delay = 2.0
        last_error = None

        for attempt in range(max_retries):
            try:
                from langchain_core.messages import HumanMessage
                from langchain_core.runnables import RunnableConfig
                from receipt_agent.tools.label_harmonizer_models import OutlierDecision

                messages = [HumanMessage(content=prompt)]

                # LangSmith captures full prompt/response - no need to log here
                # Add LangSmith metadata for prompt comparison
                tags = [
                    "label-harmonizer-v2",
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
                        "prompt_version": "v2-trace-nesting-full-context",
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

                json_schema = OutlierDecision.model_json_schema()
                llm_with_schema = ChatOllama(
                    model=self.llm.model,
                    base_url=self.llm.base_url,
                    client_kwargs=self.llm.client_kwargs,
                    format=json_schema,
                    temperature=self.llm.temperature,
                )
                llm_structured = llm_with_schema.with_structured_output(OutlierDecision)

                response = await llm_structured.ainvoke(messages, config=config)
                is_outlier_result = getattr(response, 'is_outlier', False)
                reasoning = getattr(response, 'reasoning', "") or ""

                # Log decision only (LangSmith has full response)
                logger.debug(
                    "Outlier check: word='%s' is_outlier=%s",
                    word.word_text,
                    is_outlier_result,
                )

                return is_outlier_result

            except Exception as e:
                last_error = e
                error_str = str(e)

                is_retryable = (
                    "429" in error_str or
                    "500" in error_str or
                    "503" in error_str or
                    "502" in error_str or
                    "timeout" in error_str.lower() or
                    "rate limit" in error_str.lower() or
                    "rate_limit" in error_str.lower() or
                    "too many requests" in error_str.lower() or
                    "service unavailable" in error_str.lower()
                )

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff with jitter to prevent thundering herd
                    # attempt 0: 2-4s, attempt 1: 4-8s, attempt 2: 8-16s
                    jitter = random.uniform(0, base_delay)
                    wait_time = (base_delay * (2 ** attempt)) + jitter
                    logger.warning(
                        f"Retryable error for '{word.word_text}' "
                        f"(attempt {attempt + 1}/{max_retries}): {error_str[:100]}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    if attempt >= max_retries - 1:
                        logger.error(
                            f"LLM call failed after {max_retries} attempts for '{word.word_text}': {error_str}"
                        )
                    raise RuntimeError(
                        f"Failed to get LLM decision for '{word.word_text}': {error_str}"
                    ) from e

        raise RuntimeError(
            f"Unexpected error: Failed to get LLM decision for '{word.word_text}'"
        ) from last_error

    @traceable
    async def _suggest_label_type_for_outlier(
        self,
        word: LabelRecord,
        group: MerchantLabelGroup,
    ) -> Optional[str]:
        """
        Use LLM to suggest the correct CORE_LABEL type for an outlier.

        For example, if "SPROUTS" in "SPROUTS CREAM CHEESE" is flagged as an outlier
        in MERCHANT_NAME, this will suggest PRODUCT_NAME as the correct label type.

        This function is decorated with @traceable. When called with
        langsmith_extra={"parent": run_tree}, it will properly nest.
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
        existing_label_types: set = set()
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
            existing_labels_section = "\n## Existing Labels for This Word\n\n"
            existing_labels_section += f"This word already has the following label types: {', '.join(sorted(existing_label_types))}\n"
            existing_labels_section += "Consider whether the suggested label type should be one of these, or if a new label type is needed.\n"

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
  {chr(10).join(surrounding_lines) if surrounding_lines else "N/A"}
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
- `reasoning`: optional string explaining why this label type was suggested"""

        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.runnables import RunnableConfig
            from receipt_agent.tools.label_harmonizer_models import LabelTypeSuggestion

            messages = [HumanMessage(content=prompt)]

            # LangSmith config with comprehensive metadata
            config = RunnableConfig(
                run_name="suggest_label_type_for_outlier",
                metadata={
                    "prompt_version": "v2-trace-nesting-full-context",
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
                tags=[
                    "label-harmonizer-v2",
                    "label-type-suggestion",
                    "outlier",
                    f"merchant:{group.merchant_name}",
                    f"current-label-type:{group.label_type}",
                ],
            )

            json_schema = LabelTypeSuggestion.model_json_schema()
            llm_with_schema = ChatOllama(
                model=self.llm.model,
                base_url=self.llm.base_url,
                client_kwargs=self.llm.client_kwargs,
                format=json_schema,
                temperature=self.llm.temperature,
            )
            llm_structured = llm_with_schema.with_structured_output(LabelTypeSuggestion)

            response = await llm_structured.ainvoke(messages, config=config)
            suggested_type = getattr(response, 'suggested_label_type', None)
            reasoning = getattr(response, 'reasoning', "") or ""

            # Validate it's a CORE_LABEL
            if suggested_type and suggested_type in CORE_LABELS:
                logger.debug(
                    "LLM suggestion: word='%s' suggested='%s' reason='%s'",
                    word.word_text,
                    suggested_type,
                    reasoning[:100] if reasoning else "N/A",
                )

                # CRITICAL: Validate suggestion with similarity search + LLM validation
                # This prevents garbage suggestions like "Vons." -> WEBSITE
                # by finding similar words with the suggested label and having
                # the LLM verify the suggestion makes sense
                is_valid, _similar_words = await self._validate_suggestion_with_similarity(
                    word=word,
                    suggested_label_type=suggested_type,
                    merchant_name=group.merchant_name,
                    llm_reason=reasoning,
                )

                if is_valid:
                    logger.info(
                        "VALIDATED suggestion: '%s' -> %s",
                        word.word_text, suggested_type
                    )
                    return suggested_type
                else:
                    logger.warning(
                        "REJECTED suggestion: '%s' -> %s (LLM validation failed, will mark NEEDS_REVIEW)",
                        word.word_text, suggested_type
                    )
                    # Return None so it gets marked as NEEDS_REVIEW instead of auto-updating
                    return None

            elif suggested_type is None:
                logger.debug("Label suggestion: word='%s' suggested=None", word.word_text)
                return None
            else:
                logger.warning(
                    "Invalid label type suggested: '%s' (not in CORE_LABELS)",
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

        This is the public API for analyzing a single group. It creates a
        LangSmith trace for proper nesting of all child operations.

        Args:
            group: The merchant group to analyze
            use_similarity: Whether to use ChromaDB similarity clustering

        Returns:
            List of HarmonizerResult for each label in the group
        """
        if HAS_LANGSMITH and ls:
            # Create trace for this group analysis
            async with ls.trace(
                f"analyze-group-{group.merchant_name}-{group.label_type}",
                run_type="chain",
                metadata={
                    "merchant_name": group.merchant_name,
                    "label_type": group.label_type,
                    "labels_count": len(group.labels),
                    "use_similarity": use_similarity,
                },
                tags=["label-harmonizer-v2", "analyze-group"],
            ) as root:
                try:
                    results = await self._analyze_group_with_trace(group, use_similarity, run_tree=root)
                    root.end(outputs={"results_count": len(results), "status": "success"})
                    return results
                except Exception as e:
                    # Ensure trace is properly ended even on error
                    root.end(outputs={"status": "error", "error": str(e)})
                    raise
        else:
            # No LangSmith - run without trace
            return await self._analyze_group_with_trace(group, use_similarity, run_tree=None)

    async def _analyze_group_with_trace(
        self,
        group: MerchantLabelGroup,
        use_similarity: bool,
        run_tree: Any,
    ) -> list[HarmonizerResult]:
        """
        Analyze a merchant group with proper trace nesting.

        All child calls pass run_tree via langsmith_extra for proper nesting.
        """
        results = []

        # Step 1: Compute consensus
        self._compute_consensus(group)

        # Step 2: Find similar words
        if use_similarity:
            self._find_similar_words(group)

        # Step 3: Identify outliers (pass run_tree for trace nesting)
        await self._identify_outliers(group, run_tree=run_tree)

        # Step 4: Determine canonical label
        canonical_label = group.consensus_label

        if not canonical_label:
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
                ))
            return results

        # Step 5: Process each label
        for record in group.labels:
            changes = []
            suggested_label_type = None
            is_outlier = record in group.outliers

            if is_outlier:
                changes.append(f"OUTLIER: '{record.word_text}' doesn't belong in {group.label_type}")
                # Call with langsmith_extra for proper trace nesting
                if HAS_LANGSMITH and run_tree:
                    suggested_label_type = await self._suggest_label_type_for_outlier(
                        word=record,
                        group=group,
                        langsmith_extra={"parent": run_tree},  # type: ignore[call-arg]
                    )
                else:
                    suggested_label_type = await self._suggest_label_type_for_outlier(
                        word=record,
                        group=group,
                    )

            if record.label != canonical_label:
                changes.append(f"label: {record.label} → {canonical_label}")

            # Calculate confidence
            confidence = min(100.0, len(group.labels) * 5)
            if is_outlier:
                confidence = max(0.0, confidence - 50)
            elif record.validation_status == "VALID" and record.label == canonical_label:
                confidence = 100.0

            cluster_size = 1
            for cluster in group.similarity_clusters:
                if record in cluster:
                    cluster_size = len(cluster)
                    break

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

        Uses a single LangSmith trace for the entire harmonization run.
        All child operations properly nest under the root trace.
        """
        # Load labels if needed
        if not self._merchant_groups or self._label_type != label_type:
            self.load_labels_by_type(label_type, batch_size=batch_size, max_merchants=max_merchants)

        all_results: list[HarmonizerResult] = []
        group_summaries: list[dict] = []

        groups_to_process = list(self._merchant_groups.values())
        if limit:
            groups_to_process = groups_to_process[:limit]

        # Use async with ls.trace() for the root trace
        # All child operations will nest under this trace
        if HAS_LANGSMITH and ls:
            async with ls.trace(
                f"harmonize-{label_type}",
                run_type="chain",
                metadata={
                    "label_type": label_type,
                    "total_groups": len(groups_to_process),
                    "total_labels": sum(len(g.labels) for g in groups_to_process),
                },
                tags=["label-harmonizer-v2", f"label-type:{label_type}"],
            ) as root:
                try:
                    for i, group in enumerate(groups_to_process):
                        if i > 0 and i % 10 == 0:
                            logger.info("Progress: %d/%d groups", i, len(groups_to_process))

                        # Pass root run_tree to child operations
                        results = await self._analyze_group_with_trace(
                            group, use_similarity, run_tree=root
                        )
                        all_results.extend(results)

                        # Summarize
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

                    # Add final metadata to trace
                    root.metadata["total_results"] = len(all_results)
                    root.metadata["total_needing_update"] = sum(1 for r in all_results if r.needs_update)
                    root.end(outputs={"status": "completed", "results_count": len(all_results)})
                except Exception as e:
                    # Ensure trace is properly ended even on error
                    root.end(outputs={"status": "error", "error": str(e)})
                    raise
        else:
            # No LangSmith - run without tracing
            for i, group in enumerate(groups_to_process):
                if i > 0 and i % 10 == 0:
                    logger.info("Progress: %d/%d groups", i, len(groups_to_process))

                results = await self._analyze_group_with_trace(
                    group, use_similarity, run_tree=None
                )
                all_results.extend(results)

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

        # Convert results to dicts
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
        """Apply harmonization fixes to DynamoDB."""
        if not self._last_report:
            raise ValueError("Must call harmonize_label_type() first")

        result = UpdateResult()
        labels_to_update = []

        for r in self._last_report["labels"]:
            if not r["needs_update"]:
                result.total_skipped += 1
                continue

            if r["confidence"] < min_confidence:
                result.total_skipped += 1
                continue

            if r["group_size"] < min_group_size:
                result.total_skipped += 1
                continue

            labels_to_update.append(r)

        result.total_processed = len(labels_to_update)

        if dry_run:
            logger.info("Dry run: would_update=%d", len(labels_to_update))
            result.total_updated = len(labels_to_update)
            return result

        # Actually apply updates
        logger.info("Applying updates: count=%d", len(labels_to_update))

        for r in labels_to_update:
            try:
                label = self.dynamo.get_receipt_word_label(
                    r["image_id"],
                    r["receipt_id"],
                    r["line_id"],
                    r["word_id"],
                    r["current_label"],
                )

                if not label:
                    result.total_failed += 1
                    result.errors.append(f"Label not found: {r['image_id']}")
                    continue

                # Handle label type change
                if r.get("suggested_label_type") and r["suggested_label_type"] != r["label_type"]:
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    label.validation_status = ValidationStatus.INVALID.value
                    self.dynamo.update_receipt_word_label(label)

                    new_label = ReceiptWordLabel(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=r["suggested_label_type"],
                        reasoning=f"Label type corrected from {r['label_type']}",
                        timestamp_added=datetime.now().isoformat(),
                        validation_status=ValidationStatus.PENDING.value,
                        label_proposed_by="label-harmonizer-v2",
                        label_consolidated_from=label.label,
                    )
                    self.dynamo.add_receipt_word_label(new_label)
                    result.total_updated += 1

                elif r["consensus_label"] and label.label != r["consensus_label"]:
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    label.validation_status = ValidationStatus.SUPERSEDED.value
                    self.dynamo.update_receipt_word_label(label)

                    new_label = ReceiptWordLabel(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=r["consensus_label"],
                        reasoning=f"Updated to consensus",
                        timestamp_added=datetime.now().isoformat(),
                        validation_status=ValidationStatus.VALID.value,
                        label_proposed_by="label-harmonizer-v2",
                        label_consolidated_from=label.label,
                    )
                    self.dynamo.add_receipt_word_label(new_label)
                    result.total_updated += 1

            except Exception as e:
                logger.error(f"Failed to update: {e}")
                result.total_failed += 1
                result.errors.append(str(e))

        logger.info(
            "Update complete: updated=%d failed=%d",
            result.total_updated,
            result.total_failed,
        )
        return result

    async def apply_fixes_from_results(
        self,
        results: list[HarmonizerResult],
        label_type: str,
        dry_run: bool = True,
        min_confidence: float = 70.0,
        min_group_size: int = 3,
    ) -> UpdateResult:
        """
        Apply harmonization fixes directly from HarmonizerResult list.

        This is an alternative to apply_fixes() that works with results from
        analyze_group() without needing to call harmonize_label_type() first.

        Outliers WITHOUT a validated suggested_label_type are marked as NEEDS_REVIEW.
        Outliers WITH a validated suggested_label_type get a new label created.
        """
        result = UpdateResult()
        labels_to_update = []
        labels_for_review = []  # Outliers without validated suggestions

        for r in results:
            if not r.needs_update:
                result.total_skipped += 1
                continue

            # Check if this is an outlier without a validated suggestion
            is_outlier = any("OUTLIER" in change for change in r.changes_needed)
            has_validated_suggestion = r.suggested_label_type is not None

            if is_outlier and not has_validated_suggestion:
                # Outlier without validated suggestion -> NEEDS_REVIEW
                labels_for_review.append(r)
                continue

            if r.confidence < min_confidence:
                result.total_skipped += 1
                continue

            if r.group_size < min_group_size:
                result.total_skipped += 1
                continue

            labels_to_update.append(r)

        result.total_processed = len(labels_to_update) + len(labels_for_review)

        if dry_run:
            logger.info(
                "Dry run: would_update=%d, would_mark_needs_review=%d",
                len(labels_to_update), len(labels_for_review)
            )
            result.total_updated = len(labels_to_update)
            result.total_needs_review = len(labels_for_review)
            return result

        # Mark unvalidated outliers as NEEDS_REVIEW
        if labels_for_review:
            logger.info("Marking %d outliers as NEEDS_REVIEW", len(labels_for_review))
            from receipt_dynamo.constants import ValidationStatus

            for r in labels_for_review:
                try:
                    label = self.dynamo.get_receipt_word_label(
                        r.image_id,
                        r.receipt_id,
                        r.line_id,
                        r.word_id,
                        r.current_label,
                    )
                    if label and label.validation_status != ValidationStatus.NEEDS_REVIEW.value:
                        label.validation_status = ValidationStatus.NEEDS_REVIEW.value
                        self.dynamo.update_receipt_word_label(label)
                        result.total_needs_review = getattr(result, 'total_needs_review', 0) + 1
                        logger.debug(
                            "Marked as NEEDS_REVIEW: %s (outlier without validated suggestion)",
                            r.image_id
                        )
                except Exception as e:
                    logger.error("Failed to mark NEEDS_REVIEW: %s: %s", r.image_id, e)
                    result.total_failed += 1
                    result.errors.append(str(e))

        # Actually apply updates (labels with validated suggestions or consensus changes)
        logger.info("Applying updates: count=%d", len(labels_to_update))

        for r in labels_to_update:
            try:
                label = self.dynamo.get_receipt_word_label(
                    r.image_id,
                    r.receipt_id,
                    r.line_id,
                    r.word_id,
                    r.current_label,
                )

                if not label:
                    result.total_failed += 1
                    result.errors.append(f"Label not found: {r.image_id}")
                    continue

                # Handle label type change (outlier with VALIDATED suggested new type)
                if r.suggested_label_type and r.suggested_label_type != label_type:
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    # Mark old label as INVALID
                    label.validation_status = ValidationStatus.INVALID.value
                    self.dynamo.update_receipt_word_label(label)

                    # Create new label with suggested type
                    new_label = ReceiptWordLabel(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=r.suggested_label_type,
                        reasoning=f"Label type corrected from {label_type}",
                        timestamp_added=datetime.now().isoformat(),
                        validation_status=ValidationStatus.PENDING.value,
                        label_proposed_by="label-harmonizer-v2",
                        label_consolidated_from=label.label,
                    )
                    self.dynamo.add_receipt_word_label(new_label)
                    result.total_updated += 1

                elif r.consensus_label and label.label != r.consensus_label:
                    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
                    from receipt_dynamo.constants import ValidationStatus
                    from datetime import datetime

                    # Mark old label as SUPERSEDED
                    label.validation_status = ValidationStatus.SUPERSEDED.value
                    self.dynamo.update_receipt_word_label(label)

                    # Create new label with consensus
                    new_label = ReceiptWordLabel(
                        image_id=label.image_id,
                        receipt_id=label.receipt_id,
                        line_id=label.line_id,
                        word_id=label.word_id,
                        label=r.consensus_label,
                        reasoning=f"Updated to consensus",
                        timestamp_added=datetime.now().isoformat(),
                        validation_status=ValidationStatus.VALID.value,
                        label_proposed_by="label-harmonizer-v2",
                        label_consolidated_from=label.label,
                    )
                    self.dynamo.add_receipt_word_label(new_label)
                    result.total_updated += 1

            except Exception as e:
                logger.error("Failed to update %s: %s", r.image_id, e)
                result.total_failed += 1
                result.errors.append(str(e))

        logger.info(
            "Update complete: updated=%d failed=%d",
            result.total_updated,
            result.total_failed,
        )
        return result

    def print_summary(self, report: dict[str, Any]) -> None:
        """Print a human-readable summary of the harmonization report."""
        stats = report["stats"]
        summaries = report["summary"]

        print("=" * 70)
        print(f"LABEL HARMONIZER V2 REPORT ({stats['label_type']})")
        print("=" * 70)
        print(f"Total labels: {stats['total_labels']}")
        print(f"  With merchant: {stats['labels_with_merchant']}")
        print(f"  Without merchant: {stats['labels_without_merchant']}")
        print(f"Merchant groups: {stats['merchant_groups']}")
        print()

        total_with_merchant = stats['labels_with_merchant']
        if total_with_merchant > 0:
            consistent = sum(s["consistent"] for s in summaries)
            needs_update = sum(s["needs_update"] for s in summaries)
            print(f"Consistency:")
            print(f"  ✅ Consistent: {consistent} ({consistent/total_with_merchant*100:.1f}%)")
            print(f"  ⚠️  Needs update: {needs_update} ({needs_update/total_with_merchant*100:.1f}%)")

