"""ChromaDB-based label classifier for receipt words.

This module provides semantic label lookup using ChromaDB embeddings.
It's designed to work alongside LayoutLM, handling labels that require
semantic understanding rather than spatial/format patterns.

Usage:
    # Load from S3 snapshot
    with ChromaLabelLookup.from_s3(bucket="my-chroma-bucket") as labeler:
        label, confidence = labeler.lookup_label("ORGANIC BANANAS")

    # Or with existing client
    labeler = ChromaLabelLookup(chroma_client)
    labeled_words = labeler.label_line_items(words, amount_positions)
"""

import logging
import tempfile
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)


@dataclass
class LabelMatch:
    """Result of a ChromaDB label lookup."""
    word_text: str
    predicted_label: Optional[str]
    confidence: float
    top_matches: List[Tuple[str, str, float]]  # (matched_text, label, distance)


@dataclass
class ChromaLabelConfig:
    """Configuration for ChromaDB label lookup."""
    # Number of similar words to fetch
    n_results: int = 10

    # Minimum confidence to assign a label (0-1)
    min_confidence: float = 0.5

    # Maximum distance for a match to count (lower = stricter)
    max_distance: float = 0.5

    # Minimum weighted votes to assign label
    min_votes: float = 2.0

    # Labels to consider (None = all non-O labels)
    target_labels: Optional[List[str]] = None

    # Collection name in ChromaDB
    collection_name: str = "words"


class ChromaLabelLookup:
    """Semantic label lookup using ChromaDB embeddings.

    This class can be used in two ways:

    1. With an existing ChromaClient (you manage the lifecycle):
        client = ChromaClient(persist_directory="/path/to/db")
        labeler = ChromaLabelLookup(client)
        # Use labeler...

    2. Using the from_s3() factory (managed lifecycle with context manager):
        with ChromaLabelLookup.from_s3(bucket="my-bucket") as labeler:
            # Use labeler...
        # Cleanup happens automatically
    """

    # Labels that benefit from semantic matching
    SEMANTIC_LABELS = [
        "PRODUCT_NAME",
        "QUANTITY",
        "DISCOUNT",
        "COUPON",
        "LOYALTY_ID",
    ]

    def __init__(
        self,
        chroma_client: Any,
        config: Optional[ChromaLabelConfig] = None,
        _temp_dir: Optional[str] = None,
        _owns_client: bool = False,
    ):
        """Initialize the label lookup.

        Args:
            chroma_client: ChromaDB client instance
            config: Configuration options
            _temp_dir: Internal - temp directory to cleanup (used by from_s3)
            _owns_client: Internal - if True, close client on cleanup
        """
        self.chroma = chroma_client
        self.config = config or ChromaLabelConfig()
        self._temp_dir = _temp_dir
        self._owns_client = _owns_client

    @classmethod
    def from_s3(
        cls,
        bucket: Optional[str] = None,
        collection: str = "words",
        config: Optional[ChromaLabelConfig] = None,
        s3_client: Optional[Any] = None,
    ) -> "ChromaLabelLookup":
        """Create a labeler by downloading ChromaDB snapshot from S3.

        This method downloads the latest snapshot from S3, creates a
        ChromaClient, and returns a labeler that owns the client lifecycle.

        MUST be used as a context manager to ensure proper cleanup:

            with ChromaLabelLookup.from_s3() as labeler:
                result = labeler.lookup_label("ORGANIC BANANAS")
            # Cleanup happens automatically

        The bucket name is resolved in this order:
        1. Explicit bucket parameter
        2. CHROMADB_BUCKET environment variable
        3. CHROMADB_BUCKET_NAME environment variable

        Note: Requires OPENAI_API_KEY environment variable for embedding
        the query text.

        Args:
            bucket: S3 bucket containing ChromaDB snapshots (optional,
                   falls back to CHROMADB_BUCKET env var)
            collection: Collection name ("words" or "lines")
            config: Optional labeler configuration
            s3_client: Optional boto3 S3 client

        Returns:
            ChromaLabelLookup instance (use as context manager)

        Raises:
            RuntimeError: If snapshot download fails or bucket not configured
            ImportError: If receipt_chroma is not installed
        """
        import os
        # Import here to avoid circular imports and make dependency optional
        try:
            from receipt_chroma import ChromaClient
            from receipt_chroma.s3.snapshot import download_snapshot_atomic
        except ImportError as e:
            raise ImportError(
                "receipt_chroma package required for S3 snapshot loading. "
                "Install with: pip install -e receipt_chroma"
            ) from e

        # Resolve bucket name
        resolved_bucket = (
            bucket
            or os.environ.get("CHROMADB_BUCKET")
            or os.environ.get("CHROMADB_BUCKET_NAME")
        )
        if not resolved_bucket:
            raise RuntimeError(
                "ChromaDB bucket not configured. Either pass bucket parameter "
                "or set CHROMADB_BUCKET environment variable."
            )

        # Create temp directory for snapshot
        temp_dir = tempfile.mkdtemp(prefix="chroma_labeler_")

        logger.info(
            "Downloading ChromaDB snapshot: bucket=%s, collection=%s, "
            "local_path=%s",
            resolved_bucket,
            collection,
            temp_dir,
        )

        # Download snapshot
        result = download_snapshot_atomic(
            bucket=resolved_bucket,
            collection=collection,
            local_path=temp_dir,
            verify_integrity=True,
            s3_client=s3_client,
        )

        if result.get("status") != "downloaded":
            # Cleanup temp dir on failure
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError(
                f"Failed to download ChromaDB snapshot: {result.get('error')}"
            )

        logger.info(
            "Downloaded snapshot: version=%s, local_path=%s",
            result.get("version_id"),
            temp_dir,
        )

        # Create ChromaClient in write mode (for query_texts support)
        # Note: This requires OPENAI_API_KEY env var for embedding queries
        chroma_client = ChromaClient(
            persist_directory=temp_dir,
            mode="write",  # Need write mode for text-based queries
        )

        # Update config with collection name if not set
        effective_config = config or ChromaLabelConfig()
        if effective_config.collection_name == "words":
            effective_config.collection_name = collection

        return cls(
            chroma_client=chroma_client,
            config=effective_config,
            _temp_dir=temp_dir,
            _owns_client=True,
        )

    def __enter__(self) -> "ChromaLabelLookup":
        """Enter context manager."""
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """Exit context manager and cleanup resources."""
        self.close()

    def close(self) -> None:
        """Close the labeler and cleanup resources.

        If this labeler owns the ChromaClient (created via from_s3),
        the client will be closed and temp directory cleaned up.
        """
        if self._owns_client and self.chroma is not None:
            try:
                self.chroma.close()
            except Exception as e:
                logger.warning("Error closing ChromaClient: %s", e)
            self.chroma = None

        if self._temp_dir is not None:
            import shutil
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                logger.debug("Cleaned up temp directory: %s", self._temp_dir)
            except Exception as e:
                logger.warning("Error cleaning up temp directory: %s", e)
            self._temp_dir = None

    def lookup_label(
        self,
        word_text: str,
        word_embedding: Optional[List[float]] = None,
    ) -> LabelMatch:
        """Look up the most likely label for a word using ChromaDB.

        Args:
            word_text: The text of the word to classify
            word_embedding: Optional pre-computed embedding

        Returns:
            LabelMatch with predicted label and confidence
        """
        # Query ChromaDB
        query_args = {
            "collection_name": self.config.collection_name,
            "n_results": self.config.n_results,
            "include": ["metadatas", "documents", "distances"],
        }

        if word_embedding is not None:
            query_args["query_embeddings"] = [word_embedding]
        else:
            query_args["query_texts"] = [word_text]

        # Filter to target labels if specified
        if self.config.target_labels:
            query_args["where"] = {
                "label": {"$in": self.config.target_labels}
            }

        results = self.chroma.query(**query_args)

        # Process results
        return self._process_results(word_text, results)

    def _process_results(
        self,
        word_text: str,
        results: Dict[str, Any],
    ) -> LabelMatch:
        """Process ChromaDB results into a label prediction."""

        if not results.get("metadatas") or not results["metadatas"][0]:
            return LabelMatch(
                word_text=word_text,
                predicted_label=None,
                confidence=0.0,
                top_matches=[],
            )

        metadatas = results["metadatas"][0]
        documents = results.get("documents", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # Collect label votes weighted by inverse distance
        label_votes: Dict[str, float] = {}
        top_matches: List[Tuple[str, str, float]] = []

        for i, metadata in enumerate(metadatas):
            label = metadata.get("label")
            distance = distances[i] if i < len(distances) else 1.0
            doc_text = documents[i] if i < len(documents) else ""

            # Skip O labels and labels beyond max distance
            if not label or label == "O":
                continue
            if distance > self.config.max_distance:
                continue

            # Weight by inverse distance (closer = higher weight)
            weight = 1.0 / (1.0 + distance)
            label_votes[label] = label_votes.get(label, 0) + weight

            top_matches.append((doc_text, label, distance))

        # Determine best label
        if not label_votes:
            return LabelMatch(
                word_text=word_text,
                predicted_label=None,
                confidence=0.0,
                top_matches=top_matches[:5],
            )

        best_label = max(label_votes, key=label_votes.get)
        total_votes = sum(label_votes.values())
        confidence = label_votes[best_label] / total_votes if total_votes > 0 else 0

        # Check thresholds
        if confidence < self.config.min_confidence:
            return LabelMatch(
                word_text=word_text,
                predicted_label=None,
                confidence=confidence,
                top_matches=top_matches[:5],
            )

        if label_votes[best_label] < self.config.min_votes:
            return LabelMatch(
                word_text=word_text,
                predicted_label=None,
                confidence=confidence,
                top_matches=top_matches[:5],
            )

        return LabelMatch(
            word_text=word_text,
            predicted_label=best_label,
            confidence=confidence,
            top_matches=top_matches[:5],
        )

    def label_line_items(
        self,
        words: List[Dict[str, Any]],
        amount_bboxes: List[Dict[str, float]],
        y_threshold: float = 15.0,
    ) -> List[LabelMatch]:
        """Label words that appear on the same line as AMOUNT tokens.

        Args:
            words: List of word dicts with 'text', 'bbox' (x, y, w, h)
            amount_bboxes: List of bounding boxes for detected AMOUNT tokens
            y_threshold: Max y-distance to consider "same line"

        Returns:
            List of LabelMatch for candidate words
        """
        results = []

        for amount_bbox in amount_bboxes:
            amount_y = amount_bbox.get("y", 0)
            amount_x = amount_bbox.get("x", 0)

            # Find words on same line, to the LEFT of amount
            candidates = []
            for word in words:
                word_bbox = word.get("bbox", {})
                word_y = word_bbox.get("y", 0)
                word_x = word_bbox.get("x", 0)
                word_text = word.get("text", "")

                # Skip if not on same line
                if abs(word_y - amount_y) > y_threshold:
                    continue

                # Skip if not to the left of amount
                if word_x >= amount_x:
                    continue

                # Skip if already has a label from LayoutLM
                if word.get("label") and word["label"] != "O":
                    continue

                # Skip empty or very short text
                if not word_text or len(word_text) < 2:
                    continue

                # Skip if it looks like a number/price (handled by position rules)
                if self._looks_like_number(word_text):
                    continue

                candidates.append(word)

            # Query ChromaDB for each candidate
            for word in candidates:
                match = self.lookup_label(word["text"])
                results.append(match)

        return results

    def _looks_like_number(self, text: str) -> bool:
        """Check if text looks like a number or price."""
        # Remove common price characters
        cleaned = text.replace("$", "").replace(",", "").replace(".", "")
        return cleaned.isdigit()

    def batch_lookup(
        self,
        word_texts: List[str],
    ) -> List[LabelMatch]:
        """Look up labels for multiple words.

        Args:
            word_texts: List of word texts to classify

        Returns:
            List of LabelMatch results
        """
        # ChromaDB supports batch queries
        if not word_texts:
            return []

        query_args = {
            "collection_name": self.config.collection_name,
            "n_results": self.config.n_results,
            "query_texts": word_texts,
            "include": ["metadatas", "documents", "distances"],
        }

        if self.config.target_labels:
            query_args["where"] = {
                "label": {"$in": self.config.target_labels}
            }

        results = self.chroma.query(**query_args)

        # Process each result
        matches = []
        for i, word_text in enumerate(word_texts):
            # Extract results for this word
            word_results = {
                "metadatas": [results["metadatas"][i]] if results.get("metadatas") else [[]],
                "documents": [results["documents"][i]] if results.get("documents") else [[]],
                "distances": [results["distances"][i]] if results.get("distances") else [[]],
            }
            match = self._process_results(word_text, word_results)
            matches.append(match)

        return matches


def combine_layoutlm_and_chroma(
    layoutlm_predictions: List[Dict[str, Any]],
    chroma_labeler: ChromaLabelLookup,
    amount_label: str = "AMOUNT",
) -> List[Dict[str, Any]]:
    """Combine LayoutLM predictions with ChromaDB label lookup.

    Args:
        layoutlm_predictions: List of word predictions from LayoutLM
            Each dict has: text, bbox, label, confidence
        chroma_labeler: ChromaDB label lookup instance
        amount_label: Label name for amounts (default: "AMOUNT")

    Returns:
        Enhanced predictions with ChromaDB labels filled in
    """
    # Find AMOUNT token positions
    amount_bboxes = [
        p["bbox"] for p in layoutlm_predictions
        if p.get("label") == amount_label
    ]

    if not amount_bboxes:
        return layoutlm_predictions

    # Get ChromaDB labels for unlabeled words near amounts
    chroma_matches = chroma_labeler.label_line_items(
        words=layoutlm_predictions,
        amount_bboxes=amount_bboxes,
    )

    # Create lookup by word text
    chroma_by_text = {m.word_text: m for m in chroma_matches if m.predicted_label}

    # Enhance predictions
    enhanced = []
    for pred in layoutlm_predictions:
        new_pred = pred.copy()

        # If no label from LayoutLM, check ChromaDB
        if pred.get("label") in [None, "O"]:
            chroma_match = chroma_by_text.get(pred.get("text"))
            if chroma_match:
                new_pred["label"] = chroma_match.predicted_label
                new_pred["confidence"] = chroma_match.confidence
                new_pred["label_source"] = "chroma"
        else:
            new_pred["label_source"] = "layoutlm"

        enhanced.append(new_pred)

    return enhanced
