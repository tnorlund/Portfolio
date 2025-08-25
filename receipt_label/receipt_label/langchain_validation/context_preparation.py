"""
Context Preparation Service for LangChain Validation
===================================================

This service handles all context preparation outside the LangChain graph,
following the three-phase optimization approach:

Phase 1: Context Preparation (outside graph) <- THIS MODULE
Phase 2: LLM Validation (minimal graph)
Phase 3: Result Processing (outside graph)

Based on the proven BatchedPromptAssembler pattern from assemble_prompt_batch_optimized.py
"""

import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptLine,
    ReceiptMetadata,
)
from receipt_dynamo.constants import ValidationStatus

from receipt_label.vector_store import (
    VectorClient,
    ChromaDBClient,
    word_to_vector_id,
)
from receipt_label.vector_store.client.snapshot_client import (
    ChromaDBSnapshotClient,
    ChromaDBCollection,
)

from .models.context import (
    ValidationContext,
    ReceiptContext,
    WordContext,
    SemanticContext,
)

logger = logging.getLogger(__name__)


@dataclass
class ContextPreparationConfig:
    """Configuration for context preparation"""

    context_lines: int = 3
    chroma_batch_size: int = 100
    similarity_threshold: float = 0.8
    max_similar_words: int = 20
    local_chroma_path: Optional[Path] = None
    verify_chroma_hash: bool = True


class ContextPreparationService:
    """
    Service for efficiently preparing validation context outside the LangChain graph.

    This service batches all database operations to minimize round-trips and
    prepares rich context data for LLM validation.
    """

    def __init__(
        self,
        dynamo_client: DynamoClient,
        chroma_client: ChromaDBClient,
        config: Optional[ContextPreparationConfig] = None,
    ):
        self.dynamo_client = dynamo_client
        self.chroma_client = chroma_client
        self.config = config or ContextPreparationConfig()

    async def prepare_validation_context(
        self, labels: List[ReceiptWordLabel]
    ) -> List[ValidationContext]:
        """
        Prepare validation context for a batch of labels using optimized batching.

        This method implements the proven BatchedPromptAssembler pattern:
        1. Batch query all target words
        2. Batch query all context lines
        3. Batch query all receipt metadata
        4. Batch query all ChromaDB embeddings
        5. Assemble all ValidationContext objects

        Args:
            labels: Labels that need validation context

        Returns:
            List of ValidationContext objects ready for LLM validation
        """
        logger.info("Preparing validation context for %d labels", len(labels))
        start_time = time.time()

        # Step 1: Batch query all target words
        target_words = await self._batch_get_target_words(labels)
        logger.info("Retrieved %d target words", len(target_words))

        # Step 2: Batch query all context lines
        context_lines_map = await self._batch_get_context_lines(labels)
        logger.info(
            "Retrieved context lines for %d receipts", len(context_lines_map)
        )

        # Step 3: Batch query all receipt metadata
        metadata_map = await self._batch_get_receipt_metadata(labels)
        logger.info("Retrieved metadata for %d receipts", len(metadata_map))

        # Step 4: Batch query ChromaDB embeddings and similarity
        semantic_context_map = await self._batch_get_semantic_context(
            target_words, labels
        )
        logger.info(
            "Retrieved semantic context for %d words",
            len(semantic_context_map),
        )

        # Step 5: Assemble all ValidationContext objects
        validation_contexts = self._assemble_validation_contexts(
            labels,
            target_words,
            context_lines_map,
            metadata_map,
            semantic_context_map,
        )

        elapsed = time.time() - start_time
        logger.info(
            "Context preparation completed in %.2fs for %d contexts",
            elapsed,
            len(validation_contexts),
        )

        return validation_contexts

    async def _batch_get_target_words(
        self, labels: List[ReceiptWordLabel]
    ) -> Dict[Tuple[str, int, int, int], ReceiptWord]:
        """Batch retrieve all target words using optimized DynamoDB batch operations."""
        # Build keys for batch retrieval
        keys = []
        for label in labels:
            keys.append(
                {
                    "PK": {"S": f"IMAGE#{label.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{label.receipt_id:05d}#LINE#{label.line_id:05d}#WORD#{label.word_id:05d}"
                    },
                }
            )

        # Use the proven batch method from DynamoClient
        words = self.dynamo_client.get_receipt_words_by_keys(keys)

        # Create lookup map
        word_map = {}
        for word in words:
            key = (word.image_id, word.receipt_id, word.line_id, word.word_id)
            word_map[key] = word

        return word_map

    async def _batch_get_context_lines(
        self, labels: List[ReceiptWordLabel]
    ) -> Dict[Tuple[str, int], List[ReceiptLine]]:
        """Batch retrieve ALL receipt lines (not just context window) for each receipt."""
        # Get unique receipts - we want ALL lines for each receipt
        unique_receipts = set()
        for label in labels:
            unique_receipts.add((label.image_id, label.receipt_id))

        logger.debug(
            "Querying ALL lines for %d unique receipts", len(unique_receipts)
        )

        # Get all lines for each receipt (not just a context window)
        context_map = {}
        for image_id, receipt_id in unique_receipts:
            try:
                # Use the DynamoDB method to get all lines for this receipt
                receipt_lines = (
                    self.dynamo_client.list_receipt_lines_from_receipt(
                        image_id=image_id, receipt_id=receipt_id
                    )
                )

                # Sort by line_id for proper display order
                receipt_lines.sort(key=lambda l: l.line_id)
                context_map[(image_id, receipt_id)] = receipt_lines

            except Exception as e:
                logger.warning(
                    "Could not retrieve lines for receipt %s-%d: %s",
                    image_id,
                    receipt_id,
                    e,
                )
                context_map[(image_id, receipt_id)] = []

        return context_map

    async def _batch_get_receipt_metadata(
        self, labels: List[ReceiptWordLabel]
    ) -> Dict[Tuple[str, int], ReceiptMetadata]:
        """Batch retrieve receipt metadata using optimized indexing."""
        # Get unique receipts
        unique_receipts = set()
        for label in labels:
            unique_receipts.add((label.image_id, label.receipt_id))

        indices = list(unique_receipts)
        logger.debug("Querying metadata for %d unique receipts", len(indices))

        # Use batch metadata retrieval
        metadata_list = self.dynamo_client.get_receipt_metadatas_by_indices(
            indices
        )

        # Create lookup map
        metadata_map = {}
        for metadata in metadata_list:
            key = (metadata.image_id, metadata.receipt_id)
            metadata_map[key] = metadata

        return metadata_map

    async def _batch_get_semantic_context(
        self,
        target_words: Dict[Tuple[str, int, int, int], ReceiptWord],
        labels: List[ReceiptWordLabel],
    ) -> Dict[str, SemanticContext]:
        """Batch retrieve semantic context from ChromaDB with similarity analysis."""
        # Build ChromaDB IDs and embeddings map, plus label mapping
        chroma_ids = []
        word_to_chroma_map = {}
        word_to_label_map = {}

        # Create mapping from word to its label
        for label in labels:
            key = (
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
            )
            word_to_label_map[key] = label.label

        for key, word in target_words.items():
            chroma_id = word_to_vector_id(word)
            chroma_ids.append(chroma_id)
            word_to_chroma_map[chroma_id] = key

        logger.debug("Querying semantic context for %d words", len(chroma_ids))

        try:
            # Check collection availability
            available_collections = self.chroma_client.list_collections()
            if "words" not in available_collections:
                logger.warning("ChromaDB 'words' collection not available")
                return {}

            # Batch retrieve embeddings
            response = self.chroma_client.get_by_ids(
                collection_name="words",
                ids=chroma_ids,
                include=["embeddings", "documents", "metadatas"],
            )

            # Build semantic context map
            semantic_context_map = {}
            if response.get("ids"):
                for i, chroma_id in enumerate(response["ids"]):
                    embedding = response["embeddings"][i]

                    # Get similar words for this embedding
                    word_key = word_to_chroma_map[chroma_id]
                    label_to_validate = word_to_label_map[word_key]
                    similar_words = await self._get_similar_words(
                        embedding,
                        chroma_id,
                        target_words[word_key],
                        label_to_validate,
                    )

                    semantic_context_map[chroma_id] = SemanticContext(
                        word_embedding=embedding,
                        chroma_id=chroma_id,
                        document_text=response["documents"][i],
                        metadata=(
                            response["metadatas"][i]
                            if response["metadatas"]
                            else None
                        ),
                        similar_valid_words=similar_words["valid"],
                        similar_invalid_words=similar_words["invalid"],
                    )

            return semantic_context_map

        except Exception as e:
            logger.error("ChromaDB semantic context error: %s", e)
            return {}

    async def _get_similar_words(
        self,
        embedding: List[float],
        current_chroma_id: str,
        target_word: ReceiptWord,
        label_to_validate: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get similar words from ChromaDB with validation status analysis."""
        try:
            # Query for similar words
            similar_results = self.chroma_client.query(
                collection_name="words",
                query_embeddings=[embedding],
                n_results=self.config.max_similar_words,
                include=["documents", "metadatas", "distances"],
            )

            valid_words = []
            invalid_words = []

            if similar_results.get("ids") and similar_results["ids"]:
                for id_, doc, metadata, distance in zip(
                    similar_results["ids"][0],
                    similar_results["documents"][0],
                    (
                        similar_results["metadatas"][0]
                        if similar_results["metadatas"]
                        else [{}] * len(similar_results["ids"][0])
                    ),
                    similar_results["distances"][0],
                ):
                    # Skip the same word
                    if id_ == current_chroma_id:
                        continue

                    if (
                        metadata
                        and distance < self.config.similarity_threshold
                    ):
                        valid_labels = metadata.get("validated_labels", "")
                        invalid_labels = metadata.get("invalid_labels", "")

                        word_info = {
                            "text": doc,
                            "distance": distance,
                            "id": id_,
                            "merchant": metadata.get(
                                "merchant_name", "Unknown"
                            ),
                        }

                        # Check if the label_to_validate appears in this word's labels
                        # This matches the logic in assemble_prompt_batch_optimized.py lines 528-539
                        if label_to_validate in valid_labels:
                            valid_words.append(word_info)
                        elif label_to_validate in invalid_labels:
                            invalid_words.append(word_info)

            return {"valid": valid_words, "invalid": invalid_words}

        except Exception as e:
            logger.error("Similar words query error: %s", e)
            return {"valid": [], "invalid": []}

    def _assemble_validation_contexts(
        self,
        labels: List[ReceiptWordLabel],
        target_words: Dict[Tuple[str, int, int, int], ReceiptWord],
        context_lines_map: Dict[Tuple[str, int], List[ReceiptLine]],
        metadata_map: Dict[Tuple[str, int], ReceiptMetadata],
        semantic_context_map: Dict[str, SemanticContext],
    ) -> List[ValidationContext]:
        """Assemble all ValidationContext objects from batched data."""
        contexts = []

        for label in labels:
            # Get target word
            word_key = (
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
            )
            target_word = target_words.get(word_key)
            if not target_word:
                logger.warning("Target word not found for label %s", label)
                continue

            # Get semantic context
            chroma_id = word_to_vector_id(target_word)
            semantic_context = semantic_context_map.get(chroma_id)
            if not semantic_context:
                logger.warning(
                    "Semantic context not found for word %s", chroma_id
                )
                continue

            # Get ALL receipt context lines (no filtering to context window)
            receipt_key = (label.image_id, label.receipt_id)
            context_lines = context_lines_map.get(receipt_key, [])

            # Get receipt metadata
            receipt_metadata = metadata_map.get(receipt_key)

            # Get target line ID from the target word
            target_line_id = target_word.line_id

            # Create ValidationContext
            validation_context = ValidationContext(
                word_context=WordContext(
                    target_word=target_word,
                    label_to_validate=label.label,
                    validation_status=label.validation_status,
                ),
                receipt_context=ReceiptContext(
                    image_id=label.image_id,
                    receipt_id=label.receipt_id,
                    context_lines=context_lines,
                    target_line_id=target_line_id,
                    receipt_metadata=receipt_metadata,
                ),
                semantic_context=semantic_context,
            )

            contexts.append(validation_context)

        logger.info(
            "Successfully assembled %d validation contexts", len(contexts)
        )
        return contexts

    def format_context_for_llm(self, context: ValidationContext) -> str:
        """
        Format ValidationContext into receipt context using proven format from assemble_prompt_batch_optimized.py

        This creates the visual receipt context with target word highlighting.
        """
        target_word = context.word_context.target_word
        target_line_id = target_word.line_id

        # Build receipt context display using proven format
        context_lines = []
        context_lines.append("📄 Receipt Context:")

        for line in context.receipt_context.context_lines:
            if not line.text:
                context_lines.append(f"  {line.line_id:2d}: [EMPTY LINE]")
                continue

            line_text = line.text

            # Mark target line and attempt to highlight target word
            is_target_line = line.line_id == target_line_id
            if is_target_line:
                # Try to highlight the target word in the line text
                if target_word.text in line_text:
                    line_text = line_text.replace(
                        target_word.text,
                        f"→{target_word.text}←",
                        1,  # Only replace first occurrence
                    )
                else:
                    # If target word not found in line text, add annotation
                    line_text = f"{line_text} [TARGET: {target_word.text}]"
                context_lines.append(
                    f"→ {line.line_id:2d}: {line_text} ← TARGET LINE"
                )
            else:
                context_lines.append(f"  {line.line_id:2d}: {line_text}")

        return "\n".join(context_lines)

    def format_merchant_info(self, receipt_metadata=None) -> str:
        """Format merchant information exactly like assemble_prompt_batch_optimized.py lines 414-425."""
        if not receipt_metadata:
            return "🏪 Merchant: Unknown Merchant"

        lines = [f"🏪 Merchant: {receipt_metadata.merchant_name}"]
        if receipt_metadata.merchant_category:
            lines.append(f"   Category: {receipt_metadata.merchant_category}")
        if receipt_metadata.address:
            lines.append(f"   Address: {receipt_metadata.address}")
        if receipt_metadata.phone_number:
            lines.append(f"   Phone: {receipt_metadata.phone_number}")
        if receipt_metadata.validation_status:
            lines.append(
                f"   Validation Status: {receipt_metadata.validation_status}"
            )
        if receipt_metadata.matched_fields:
            lines.append(
                f"   Matched Fields: {', '.join(receipt_metadata.matched_fields)}"
            )

        return "\n".join(lines)

    def format_semantic_similarity(
        self, similar_valid_words=None, similar_invalid_words=None, label=""
    ) -> str:
        """Format semantic similarity exactly like assemble_prompt_batch_optimized.py lines 436-481."""
        if not similar_valid_words:
            similar_valid_words = []
        if not similar_invalid_words:
            similar_invalid_words = []

        lines = ["🧠 SEMANTIC SIMILARITY:"]
        lines.append(
            f"  Similar words where '{label}' was VALID: ({len(similar_valid_words)} found)"
        )

        if similar_valid_words:
            for word_info in similar_valid_words[:3]:  # Show top 3
                lines.append(
                    f"    ✓ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}"
                )
        else:
            lines.append("    None found")

        lines.append(
            f"  Similar words where '{label}' was INVALID: ({len(similar_invalid_words)} found)"
        )

        if similar_invalid_words:
            for word_info in similar_invalid_words[:3]:  # Show top 3
                lines.append(
                    f"    ✗ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}"
                )
        else:
            lines.append("    None found")

        # Add suggestion exactly like lines 460-481
        valid_count = len(similar_valid_words)
        invalid_count = len(similar_invalid_words)

        if valid_count > invalid_count:
            lines.append(
                f"\n   → Suggestion: '{label}' is likely VALID for this word"
            )
            lines.append(
                f"     (Based on {valid_count} valid vs {invalid_count} invalid similar examples)"
            )
        elif invalid_count > valid_count:
            lines.append(
                f"\n   → Suggestion: '{label}' is likely INVALID for this word"
            )
            lines.append(
                f"     (Based on {invalid_count} invalid vs {valid_count} valid similar examples)"
            )
        else:
            lines.append(f"\n   → No clear suggestion - needs manual review")
            lines.append(
                f"     ({valid_count} valid vs {invalid_count} invalid similar examples)"
            )

        return "\n".join(lines)

    def format_full_validation_prompt(
        self, contexts: List[ValidationContext]
    ) -> str:
        """
        Format complete validation prompt using proven format from _format_prompt.py

        This creates the full structured prompt with schema, examples, and receipt context.
        """
        from receipt_label.constants import CORE_LABELS
        import json

        if not contexts:
            return ""

        # Use first context for receipt-level info (they should all be from same receipt)
        first_context = contexts[0]
        receipt_metadata = (
            first_context.receipt_context.receipt_metadata.__dict__
            if first_context.receipt_context.receipt_metadata
            else {}
        )

        # Schema for validate_labels function (matching proven format)
        schema = {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "is_valid": {"type": "boolean"},
                            "correct_label": {
                                "type": "string",
                                "enum": list(CORE_LABELS.keys()),
                            },
                        },
                        "required": ["id", "is_valid"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
        }

        # Build targets in proven format
        formatted_targets = []
        for context in contexts:
            target_word = context.word_context.target_word
            target_id = (
                f"IMAGE#{context.receipt_context.image_id}#"
                f"RECEIPT#{context.receipt_context.receipt_id:05d}#"
                f"LINE#{target_word.line_id:05d}#"
                f"WORD#{target_word.word_id:05d}#"
                f"LABEL#{context.word_context.label_to_validate}#"
                f"VALIDATION_STATUS#NONE"
            )
            formatted_targets.append(
                {
                    "id": target_id,
                    "text": target_word.text,
                    "line_id": target_word.line_id,
                    "proposed_label": context.word_context.label_to_validate,
                }
            )

        prompt_lines = []
        prompt_lines.append("### Schema")
        prompt_lines.append("```json")
        prompt_lines.append(json.dumps(schema, indent=2))
        prompt_lines.append("```")
        prompt_lines.append("")

        prompt_lines.append("### Task")
        merchant_name = receipt_metadata.get(
            "merchant_name", "Unknown Merchant"
        )
        prompt_lines.append(
            f'Validate these labels on one "{merchant_name}" receipt. '
            "Return **only** a call to the **`validate_labels`** function with a "
            "`results` array, and do NOT renumber the `id` field.\n"
        )

        # Add merchant information in proven format
        prompt_lines.append("### Merchant Information")
        prompt_lines.append(f"🏪 Merchant: {merchant_name}")
        if receipt_metadata.get("merchant_category"):
            prompt_lines.append(
                f"   Category: {receipt_metadata['merchant_category']}"
            )
        if receipt_metadata.get("address"):
            prompt_lines.append(f"   Address: {receipt_metadata['address']}")
        if receipt_metadata.get("phone_number"):
            prompt_lines.append(
                f"   Phone: {receipt_metadata['phone_number']}"
            )
        if receipt_metadata.get("validation_status"):
            prompt_lines.append(
                f"   Validation Status: {receipt_metadata['validation_status']}"
            )
        if receipt_metadata.get("matched_fields"):
            prompt_lines.append(
                f"   Matched Fields: {', '.join(receipt_metadata['matched_fields'])}"
            )
        prompt_lines.append("")

        prompt_lines.append("Each result object must include:")
        prompt_lines.append('- `"id"`: the original label identifier')
        prompt_lines.append('- `"is_valid"`: true or false')
        prompt_lines.append(
            '- `"correct_label"`: (only if `"is_valid": false`)'
        )

        prompt_lines.append("### Examples (for guidance only)")
        prompt_lines.append(
            'SPROUTS → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#'
            '00003#WORD#00001#LABEL#MERCHANT_NAME#VALIDATION_STATUS#NONE",'
            '"is_valid":true}]}'
        )
        prompt_lines.append(
            '4.99 → {"results":[{"id":"IMAGE#abc123#RECEIPT#00001#LINE#00010#'
            'WORD#00002#LABEL#LINE_TOTAL#VALIDATION_STATUS#NONE","is_valid":'
            'false,"correct_label":"UNIT_PRICE"}]}'
        )

        prompt_lines.append("### Allowed labels")
        prompt_lines.append(", ".join(CORE_LABELS.keys()))
        prompt_lines.append(
            "Only labels from the above list are valid; do NOT propose any other "
            "label."
        )
        prompt_lines.append("")
        
        prompt_lines.append("### OCR Data Context")
        prompt_lines.append(
            "📄 This text was extracted from a receipt image using Optical Character Recognition (OCR)."
        )
        prompt_lines.append("OCR may introduce errors such as:")
        prompt_lines.append("- Character misrecognition (e.g., 'O' vs '0', 'l' vs '1', 'S' vs '5')")
        prompt_lines.append("- Spacing issues (e.g., \"Grand Total\" → \"GrandTotal\" or \"Grand To tal\")")
        prompt_lines.append("- Partial text extraction (e.g., cut-off words at image edges)")
        prompt_lines.append("- Case sensitivity variations (e.g., \"TOTAL\" vs \"total\")")
        prompt_lines.append("")
        prompt_lines.append("When validating, consider that words might be slightly corrupted versions of the intended text.")
        prompt_lines.append("Account for potential OCR errors when determining if a label is valid.")
        prompt_lines.append("")

        prompt_lines.append("### Targets")
        prompt_lines.append(json.dumps(formatted_targets))
        prompt_lines.append("")

        # Add the visual receipt context with target highlighting
        prompt_lines.append("### Receipt")
        prompt_lines.append(
            "Below is the receipt for context with target words highlighted:"
        )
        prompt_lines.append("")

        # Use the first context to get the formatted receipt lines with highlighting
        receipt_context = self.format_context_for_llm(first_context)
        prompt_lines.append(receipt_context)
        prompt_lines.append("")

        prompt_lines.append("### END PROMPT — reply with JSON only ###")

        return "\n".join(prompt_lines)

    @classmethod
    async def ensure_chroma_snapshot(
        cls, bucket_name: str, local_path: Path, verify_hash: bool = True
    ) -> bool:
        """
        Ensure ChromaDB snapshot is available locally using infrastructure-aligned client.

        This is extracted from assemble_prompt_batch_optimized.py for reuse.
        """
        if local_path.exists() and any(local_path.iterdir()):
            logger.info("ChromaDB snapshot already exists at %s", local_path)
            return True

        try:
            logger.info("Downloading ChromaDB snapshot to %s", local_path)

            snapshot_client = ChromaDBSnapshotClient(bucket_name=bucket_name)

            result = snapshot_client.download_collection_snapshot(
                collection=ChromaDBCollection.WORDS,
                local_directory=str(local_path),
                verify_hash=verify_hash,
                hash_algorithm="md5",
            )

            if result["status"] == "downloaded":
                logger.info("Successfully downloaded ChromaDB snapshot")
                logger.info(
                    "Hash verified: %s", result.get("hash_verified", "unknown")
                )
                return True
            else:
                logger.error(
                    "Failed to download snapshot: %s", result.get("error")
                )
                return False

        except Exception as e:
            logger.error("ChromaDB snapshot download failed: %s", e)
            return False


# Convenience function for easy integration
async def prepare_contexts_for_validation(
    labels: List[ReceiptWordLabel],
    dynamo_client: DynamoClient,
    chroma_client: ChromaDBClient,
    config: Optional[ContextPreparationConfig] = None,
) -> List[ValidationContext]:
    """
    Convenience function that creates a service and prepares contexts.

    This function provides a simple interface that matches the existing
    assemble_prompt_batch_optimized.py usage pattern.
    """
    service = ContextPreparationService(dynamo_client, chroma_client, config)
    return await service.prepare_validation_context(labels)
