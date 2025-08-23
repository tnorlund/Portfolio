"""
Batch-optimized version of assemble_prompt_refactored.py

This script optimizes performance by batching all DynamoDB queries upfront:
1. Batch query all target words
2. Batch query all context lines
3. Batch query all receipt metadata
4. Batch query all ChromaDB embeddings
5. Assemble all ValidationPromptData objects
6. Display results

This approach minimizes database round-trips and maximizes throughput.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.data._pulumi import load_env
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
    SnapshotManager,
    ChromaDBClient,
    word_to_vector_id,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
LOCAL_CHROMA_WORD_PATH = Path(__file__).parent / "dev.word_chroma"
LOCAL_CHROMA_LINE_PATH = Path(__file__).parent / "dev.line_chroma"
LABEL_TO_VALIDATE = "GRAND_TOTAL"


@dataclass
class ValidationPromptData:
    """Contains all data needed to assemble a validation prompt for a single word."""

    # Target word and label information
    target_word: ReceiptWord
    label_to_validate: str

    # Semantic similarity data
    word_embedding: List[float]
    chroma_id: str

    # Receipt context using ReceiptLines
    context_lines: List[ReceiptLine]
    target_line_id: int

    # Receipt metadata with rich merchant information
    receipt_metadata: Optional[ReceiptMetadata] = None

    # Legacy compatibility
    merchant_name: Optional[str] = None
    image_id: str = ""


class BatchedPromptAssembler:
    """Efficiently assembles validation prompts by batching all DynamoDB queries."""

    def __init__(
        self, dynamo_client: DynamoClient, chroma_client: ChromaDBClient
    ):
        self.dynamo_client = dynamo_client
        self.chroma_client = chroma_client

    def assemble_prompts(
        self, labels: List[ReceiptWordLabel], context_lines: int = 3
    ) -> List[ValidationPromptData]:
        """
        Assemble validation prompts by batching all queries for maximum efficiency.

        Args:
            labels: List of labels that need validation
            context_lines: Number of lines before/after target to include

        Returns:
            List of ValidationPromptData objects
        """
        logger.info("Starting batch assembly for %d labels", len(labels))

        # Step 1: Batch query all target words
        target_words = self._batch_get_target_words(labels)
        logger.info("Retrieved %d target words", len(target_words))

        # Step 2: Batch query all context lines
        context_lines_map = self._batch_get_context_lines(
            labels, context_lines
        )
        logger.info(
            "Retrieved context lines for %d receipts", len(context_lines_map)
        )

        # Step 3: Batch query all receipt metadata
        metadata_map = self._batch_get_receipt_metadata(labels)
        logger.info("Retrieved metadata for %d receipts", len(metadata_map))

        # Step 4: Batch query ChromaDB embeddings
        embeddings_map = self._batch_get_embeddings(target_words)
        logger.info(
            "Retrieved %d embeddings from ChromaDB", len(embeddings_map)
        )

        # Step 5: Assemble all ValidationPromptData objects
        prompts = self._assemble_prompt_data(
            labels,
            target_words,
            context_lines_map,
            metadata_map,
            embeddings_map,
            context_lines,
        )
        logger.info("Assembled %d validation prompts", len(prompts))

        return prompts

    def _batch_get_target_words(
        self, labels: List[ReceiptWordLabel]
    ) -> Dict[Tuple[str, int, int, int], ReceiptWord]:
        """Batch retrieve all target words using get_receipt_words_by_keys."""
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

        # Batch retrieve words
        words = self.dynamo_client.get_receipt_words_by_keys(keys)

        # Create lookup map
        word_map = {}
        for word in words:
            key = (word.image_id, word.receipt_id, word.line_id, word.word_id)
            word_map[key] = word

        return word_map

    def _batch_get_context_lines(
        self, labels: List[ReceiptWordLabel], context_lines: int
    ) -> Dict[Tuple[str, int], List[ReceiptLine]]:
        """Batch retrieve all context lines using get_receipt_lines_by_indices."""
        # Collect all unique line indices needed
        line_indices_set = set()
        for label in labels:
            target_line_id = label.line_id
            min_line_id = max(1, target_line_id - context_lines)
            max_line_id = target_line_id + context_lines

            for line_id in range(min_line_id, max_line_id + 1):
                line_indices_set.add(
                    (label.image_id, label.receipt_id, line_id)
                )

        # Convert to list for batch query
        line_indices = list(line_indices_set)
        logger.info("Querying %d unique line indices", len(line_indices))

        # Batch retrieve lines
        receipt_lines = self.dynamo_client.get_receipt_lines_by_indices(
            line_indices
        )

        # Group by (image_id, receipt_id) for easy lookup
        context_map = defaultdict(list)
        for line in receipt_lines:
            key = (line.image_id, line.receipt_id)
            context_map[key].append(line)

        # Sort each group by line_id
        for key in context_map:
            context_map[key].sort(key=lambda l: l.line_id)

        return dict(context_map)

    def _batch_get_receipt_metadata(
        self, labels: List[ReceiptWordLabel]
    ) -> Dict[Tuple[str, int], ReceiptMetadata]:
        """Batch retrieve receipt metadata using get_receipt_metadatas_by_indices."""
        # Get unique (image_id, receipt_id) pairs
        unique_receipts = set()
        for label in labels:
            unique_receipts.add((label.image_id, label.receipt_id))

        # Convert to list for batch query
        indices = list(unique_receipts)
        logger.info("Querying metadata for %d unique receipts", len(indices))

        # Batch retrieve metadata
        metadata_list = self.dynamo_client.get_receipt_metadatas_by_indices(
            indices
        )

        # Create lookup map
        metadata_map = {}
        for metadata in metadata_list:
            key = (metadata.image_id, metadata.receipt_id)
            metadata_map[key] = metadata

        return metadata_map

    def _batch_get_embeddings(
        self, target_words: Dict[Tuple[str, int, int, int], ReceiptWord]
    ) -> Dict[str, Dict]:
        """Batch retrieve embeddings from ChromaDB."""
        # Build list of ChromaDB IDs
        chroma_ids = []
        word_to_chroma_map = {}

        for key, word in target_words.items():
            chroma_id = word_to_vector_id(word)
            chroma_ids.append(chroma_id)
            word_to_chroma_map[chroma_id] = key

        logger.info("Querying %d embeddings from ChromaDB", len(chroma_ids))

        # Batch retrieve from ChromaDB
        response = self.chroma_client.get_by_ids(
            collection_name="words",
            ids=chroma_ids,
            include=["embeddings", "documents", "metadatas"],
        )

        # Create lookup map
        embeddings_map = {}
        if response.get("ids"):
            for i, chroma_id in enumerate(response["ids"]):
                embeddings_map[chroma_id] = {
                    "embedding": response["embeddings"][i],
                    "document": response["documents"][i],
                    "metadata": (
                        response["metadatas"][i]
                        if response["metadatas"]
                        else None
                    ),
                }

        return embeddings_map

    def _assemble_prompt_data(
        self,
        labels: List[ReceiptWordLabel],
        target_words: Dict[Tuple[str, int, int, int], ReceiptWord],
        context_lines_map: Dict[Tuple[str, int], List[ReceiptLine]],
        metadata_map: Dict[Tuple[str, int], ReceiptMetadata],
        embeddings_map: Dict[str, Dict],
        context_lines: int,
    ) -> List[ValidationPromptData]:
        """Assemble all ValidationPromptData objects from batched data."""
        prompts = []

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

            # Get embedding data
            chroma_id = word_to_vector_id(target_word)
            embedding_data = embeddings_map.get(chroma_id)
            if not embedding_data:
                logger.warning("Embedding not found for word %s", chroma_id)
                continue

            # Get context lines for this receipt
            receipt_key = (label.image_id, label.receipt_id)
            all_lines = context_lines_map.get(receipt_key, [])

            # Filter to context range around target line
            target_line_id = target_word.line_id
            min_line_id = max(1, target_line_id - context_lines)
            max_line_id = target_line_id + context_lines

            filtered_context = [
                line
                for line in all_lines
                if min_line_id <= line.line_id <= max_line_id
            ]

            # Get receipt metadata
            receipt_metadata = metadata_map.get(receipt_key)

            # Extract merchant name from ChromaDB metadata (fallback)
            merchant_name = None
            if embedding_data.get("metadata"):
                merchant_name = embedding_data["metadata"].get("merchant_name")

            # Create ValidationPromptData
            prompt_data = ValidationPromptData(
                target_word=target_word,
                label_to_validate=label.label,
                word_embedding=embedding_data["embedding"],
                chroma_id=chroma_id,
                context_lines=filtered_context,
                target_line_id=target_line_id,
                receipt_metadata=receipt_metadata,
                merchant_name=merchant_name,
                image_id=label.image_id,
            )

            prompts.append(prompt_data)

        return prompts


# Display functions (reused from previous version)
def display_receipt_context_from_prompt_data(
    prompt_data: ValidationPromptData,
) -> None:
    """Display receipt context using ReceiptLines text attribute."""
    if not prompt_data.context_lines:
        print("No context lines available")
        return

    print(f"\n📄 Receipt Context (using ReceiptLines):")
    target_word = prompt_data.target_word

    for line in prompt_data.context_lines:
        if not line.text:
            print(f"  {line.line_id:2d}: [EMPTY LINE]")
            continue

        line_text = line.text

        # Mark target line and attempt to highlight target word
        is_target_line = line.line_id == prompt_data.target_line_id
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
            print(f"→ {line.line_id:2d}: {line_text} ← TARGET LINE")
        else:
            print(f"  {line.line_id:2d}: {line_text}")


def display_validation_results_from_prompt_data(
    prompt_data: ValidationPromptData,
    words_with_label_valid: List[Dict],
    words_with_label_invalid: List[Dict],
    result_index: int,
) -> None:
    """Display the validation results using ValidationPromptData."""
    target_word = prompt_data.target_word
    print(f"\n{result_index}. Word needing validation: '{target_word.text}'")
    print(f"Label being validated: {prompt_data.label_to_validate}")
    print(f"Image: {prompt_data.image_id[:8]}...")

    # Display rich metadata information if available
    if prompt_data.receipt_metadata:
        metadata = prompt_data.receipt_metadata
        print(f"🏪 Merchant: {metadata.merchant_name}")
        if metadata.merchant_category:
            print(f"   Category: {metadata.merchant_category}")
        if metadata.address:
            print(f"   Address: {metadata.address}")
        if metadata.phone_number:
            print(f"   Phone: {metadata.phone_number}")
        print(f"   Validation Status: {metadata.validation_status}")
        if metadata.matched_fields:
            print(f"   Matched Fields: {', '.join(metadata.matched_fields)}")
    elif prompt_data.merchant_name:
        print(f"Merchant: {prompt_data.merchant_name}")

    print(
        f"Receipt context: Line {target_word.line_id}, Word {target_word.word_id}"
    )

    # Display receipt context with visual alignment
    display_receipt_context_from_prompt_data(prompt_data)

    print(f"\n🧠 SEMANTIC SIMILARITY:")
    print(
        f"  Similar words where '{prompt_data.label_to_validate}' was VALID: ({len(words_with_label_valid)} found)"
    )
    if words_with_label_valid:
        for word_info in words_with_label_valid[:3]:
            print(
                f"    ✓ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}"
            )
    else:
        print("    None found")

    print(
        f"  Similar words where '{prompt_data.label_to_validate}' was INVALID: ({len(words_with_label_invalid)} found)"
    )
    if words_with_label_invalid:
        for word_info in words_with_label_invalid[:3]:
            print(
                f"    ✗ '{word_info['text']}' (distance: {word_info['distance']:.3f}) - {word_info['merchant']}"
            )
    else:
        print("    None found")

    # Suggest validation based on similar words
    valid_count = len(words_with_label_valid)
    invalid_count = len(words_with_label_invalid)

    if valid_count > invalid_count:
        print(
            f"\n   → Suggestion: '{prompt_data.label_to_validate}' is likely VALID for '{target_word.text}'"
        )
        print(
            f"     (Based on {valid_count} valid vs {invalid_count} invalid similar examples)"
        )
    elif invalid_count > valid_count:
        print(
            f"\n   → Suggestion: '{prompt_data.label_to_validate}' is likely INVALID for '{target_word.text}'"
        )
        print(
            f"     (Based on {invalid_count} invalid vs {valid_count} valid similar examples)"
        )
    else:
        print(f"\n   → No clear suggestion - needs manual review")
        print(
            f"     ({valid_count} valid vs {invalid_count} invalid similar examples)"
        )


def analyze_label_similarity(
    word_embedding: List[float],
    current_word_id: str,
    chroma_client: ChromaDBClient,
    label_to_validate: str,
    n_results: int = 20,
) -> Tuple[List[Dict], List[Dict]]:
    """Analyze similar words to determine if a label is likely valid or invalid."""
    # Query for similar words
    similar_results = chroma_client.query(
        collection_name="words",
        query_embeddings=[word_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    # Filter results for words that have LABEL_TO_VALIDATE in valid or invalid labels
    words_with_label_valid = []
    words_with_label_invalid = []

    if not similar_results.get("ids") or not similar_results["ids"]:
        logger.warning("No similar results found")
        return words_with_label_valid, words_with_label_invalid

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
        # Skip the same word by comparing IDs
        if id_ == current_word_id:
            continue

        # Check if metadata contains label info
        if metadata:
            valid_labels = metadata.get("validated_labels", "")
            invalid_labels = metadata.get("invalid_labels", "")

            # Check if LABEL_TO_VALIDATE is in valid labels
            if label_to_validate in valid_labels:
                words_with_label_valid.append(
                    {
                        "text": doc,
                        "distance": distance,
                        "id": id_,
                        "merchant": metadata.get("merchant_name", "Unknown"),
                    }
                )

            # Check if LABEL_TO_VALIDATE is in invalid labels
            elif label_to_validate in invalid_labels:
                words_with_label_invalid.append(
                    {
                        "text": doc,
                        "distance": distance,
                        "id": id_,
                        "merchant": metadata.get("merchant_name", "Unknown"),
                    }
                )

    return words_with_label_valid, words_with_label_invalid


def ensure_chroma_snapshots(pulumi_env: Dict) -> None:
    """Ensure ChromaDB snapshots exist locally, downloading from S3 if needed."""
    chroma_s3_bucket = pulumi_env.get("embedding_chromadb_bucket_name")
    if not chroma_s3_bucket:
        raise ValueError(
            "ChromaDB bucket name not found in Pulumi environment"
        )

    # Setup snapshot managers for both collections
    word_snapshot_manager = SnapshotManager(
        bucket_name=chroma_s3_bucket, s3_prefix="words/snapshot/latest/"
    )

    # Check and restore word embeddings
    if not LOCAL_CHROMA_WORD_PATH.exists() or not any(
        LOCAL_CHROMA_WORD_PATH.iterdir()
    ):
        logger.info("Downloading word embeddings from S3...")
        try:
            word_snapshot_manager.restore_snapshot(
                collection_name="words",
                local_directory=str(LOCAL_CHROMA_WORD_PATH),
                download_from_s3=True,
                create_client=False,
                verify_hash=False,
            )
            logger.info("Successfully downloaded word embeddings")
        except Exception as e:
            logger.warning(
                "Could not download word embeddings using SnapshotManager: %s",
                e,
            )


def main():
    """Main execution function with batch optimization."""
    try:
        start_time = time.time()

        # Load environment configuration
        logger.info("Loading environment configuration...")
        load_start = time.time()
        pulumi_env = load_env()
        if not pulumi_env:
            raise ValueError(
                "Pulumi environment variables not found, try adding the Pulumi API key"
            )
        logger.info("Environment loaded in %.2fs", time.time() - load_start)

        # Ensure ChromaDB snapshots are available locally
        snapshot_start = time.time()
        logger.info("Ensuring ChromaDB snapshots...")
        ensure_chroma_snapshots(pulumi_env)
        logger.info("Snapshots ready in %.2fs", time.time() - snapshot_start)

        # Create vector clients
        client_start = time.time()
        logger.info("Creating ChromaDB clients...")
        chroma_word_client = VectorClient.create_chromadb_client(
            persist_directory=str(LOCAL_CHROMA_WORD_PATH),
            mode="read",
            metadata_only=True,
        )
        logger.info("Clients created in %.2fs", time.time() - client_start)

        # Create DynamoDB client
        dynamo_start = time.time()
        logger.info("Creating DynamoDB client...")
        dynamo_client = DynamoClient(pulumi_env.get("dynamodb_table_name"))
        logger.info(
            "DynamoDB client created in %.2fs", time.time() - dynamo_start
        )

        # Query for labels that need validation
        query_start = time.time()
        logger.info("Querying for labels that need validation...")
        receipt_word_labels, last_evaluated_key = (
            dynamo_client.get_receipt_word_labels_by_label(
                label=LABEL_TO_VALIDATE,
                limit=1000,
            )
        )
        logger.info(
            "Labels query completed in %.2fs", time.time() - query_start
        )

        labels_that_need_validation = [
            label
            for label in receipt_word_labels
            if label.validation_status == ValidationStatus.NONE.value
        ]

        print(
            f"Found {len(labels_that_need_validation)} labels that need validation"
        )

        # Create batch assembler and assemble all prompts
        assembler_start = time.time()
        logger.info("Creating batch assembler...")
        assembler = BatchedPromptAssembler(dynamo_client, chroma_word_client)

        # Assemble all prompts with batched queries
        prompts = assembler.assemble_prompts(
            labels_that_need_validation, context_lines=3
        )
        logger.info(
            "Batch assembly completed in %.2fs", time.time() - assembler_start
        )

        # Process and display results
        processing_start = time.time()
        logger.info("Processing %d validation prompts...", len(prompts))

        for i, prompt_data in enumerate(prompts, 1):
            try:
                # Perform similarity analysis
                words_with_label_valid, words_with_label_invalid = (
                    analyze_label_similarity(
                        word_embedding=prompt_data.word_embedding,
                        current_word_id=prompt_data.chroma_id,
                        chroma_client=chroma_word_client,
                        label_to_validate=LABEL_TO_VALIDATE,
                    )
                )

                # Display results
                display_validation_results_from_prompt_data(
                    prompt_data=prompt_data,
                    words_with_label_valid=words_with_label_valid,
                    words_with_label_invalid=words_with_label_invalid,
                    result_index=i,
                )

            except Exception as e:
                logger.error("Error processing prompt %d: %s", i, e)
                continue

        processing_total = time.time() - processing_start
        total_time = time.time() - start_time

        logger.info("Processing completed in %.2fs", processing_total)
        logger.info("Total execution time: %.2fs", total_time)
        logger.info("Batch optimization successful!")

    except Exception as e:
        logger.error("Fatal error in main execution: %s", e)
        raise


if __name__ == "__main__":
    main()
