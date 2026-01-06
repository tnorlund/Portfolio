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
class LineItemRegion:
    """Detected line item region on a receipt."""
    y_min: float  # Top of line item region
    y_max: float  # Bottom of line item region
    line_count: int  # Number of lines detected


# Context keywords for AMOUNT subtype classification
AMOUNT_SUBTYPE_KEYWORDS = {
    "GRAND_TOTAL": [
        "total", "due", "amount due", "balance", "grand total",
        "total due", "amount", "pay", "charge",
    ],
    "SUBTOTAL": [
        "subtotal", "sub total", "sub-total", "merchandise",
        "items total", "food total",
    ],
    "TAX": [
        "tax", "hst", "gst", "pst", "vat", "sales tax",
        "state tax", "local tax", "fed tax",
    ],
    "DISCOUNT": [
        "discount", "savings", "save", "off", "coupon",
        "promo", "member savings", "you saved",
    ],
}


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


# =============================================================================
# Line Item Region Detection and Post-Processing
# =============================================================================


def group_words_by_line(
    words: List[Dict[str, Any]],
    y_threshold: float = 15.0,
) -> List[List[Dict[str, Any]]]:
    """Group words into lines based on Y-coordinate proximity.

    Args:
        words: List of word dicts with 'bbox' containing 'y' coordinate
        y_threshold: Maximum Y distance to consider words on same line

    Returns:
        List of lines, where each line is a list of words sorted by X
    """
    if not words:
        return []

    # Sort by Y, then X
    sorted_words = sorted(
        words,
        key=lambda w: (w.get("bbox", {}).get("y", 0), w.get("bbox", {}).get("x", 0))
    )

    lines: List[List[Dict[str, Any]]] = []
    current_line: List[Dict[str, Any]] = [sorted_words[0]]

    for word in sorted_words[1:]:
        word_y = word.get("bbox", {}).get("y", 0)
        line_y = current_line[0].get("bbox", {}).get("y", 0)

        if abs(word_y - line_y) <= y_threshold:
            current_line.append(word)
        else:
            # Sort current line by X and start new line
            current_line.sort(key=lambda w: w.get("bbox", {}).get("x", 0))
            lines.append(current_line)
            current_line = [word]

    # Don't forget the last line
    current_line.sort(key=lambda w: w.get("bbox", {}).get("x", 0))
    lines.append(current_line)

    return lines


def detect_line_item_region(
    words: List[Dict[str, Any]],
    amount_label: str = "AMOUNT",
    y_threshold: float = 15.0,
) -> Optional[LineItemRegion]:
    """Detect the line item region based on AMOUNT label positions.

    The line item region is the vertical span containing AMOUNT tokens,
    excluding the totals section at the bottom.

    Args:
        words: List of word dicts with 'label' and 'bbox'
        amount_label: Label name for amounts
        y_threshold: Y threshold for line grouping

    Returns:
        LineItemRegion or None if no amounts found
    """
    # Find all AMOUNT positions
    amount_words = [w for w in words if w.get("label") == amount_label]
    if not amount_words:
        return None

    # Get Y coordinates
    y_coords = [w.get("bbox", {}).get("y", 0) for w in amount_words]
    y_min = min(y_coords)
    y_max = max(y_coords)

    # Count unique lines (group by Y threshold)
    unique_y = []
    for y in sorted(y_coords):
        if not unique_y or abs(y - unique_y[-1]) > y_threshold:
            unique_y.append(y)

    return LineItemRegion(
        y_min=y_min,
        y_max=y_max,
        line_count=len(unique_y),
    )


def classify_amount_subtypes(
    words: List[Dict[str, Any]],
    amount_label: str = "AMOUNT",
    y_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Classify AMOUNT tokens into subtypes based on context.

    Uses keywords on the same line to determine if an AMOUNT is:
    - LINE_TOTAL: On same line as PRODUCT_NAME (default for line items)
    - SUBTOTAL: Near "subtotal" keyword
    - TAX: Near "tax" keyword
    - GRAND_TOTAL: Near "total" keyword or bottom-most amount
    - DISCOUNT: Near "discount", "savings" keywords

    Args:
        words: List of word dicts (will be modified in place)
        amount_label: Label name for amounts
        y_threshold: Y threshold for line grouping

    Returns:
        The same list with 'amount_subtype' added to AMOUNT words
    """
    # Group into lines
    lines = group_words_by_line(words, y_threshold)

    # Track which amounts we've classified
    classified_amounts: List[Dict[str, Any]] = []

    for line in lines:
        # Get amounts on this line
        line_amounts = [w for w in line if w.get("label") == amount_label]
        if not line_amounts:
            continue

        # Get all text on this line (lowercase for matching)
        line_text = " ".join(w.get("text", "").lower() for w in line)

        # Check for PRODUCT_NAME on the line (indicates LINE_TOTAL)
        has_product = any(
            w.get("label") == "PRODUCT_NAME" or w.get("label_source") == "chroma"
            for w in line
        )

        # Classify each amount on the line
        for amount in line_amounts:
            subtype = None

            # Check for keyword matches (order matters - more specific first)
            if any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["SUBTOTAL"]):
                # Check for "subtotal" before "total"
                subtype = "SUBTOTAL"
            elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["TAX"]):
                subtype = "TAX"
            elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["DISCOUNT"]):
                subtype = "DISCOUNT"
            elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["GRAND_TOTAL"]):
                # Only if not subtotal
                if "sub" not in line_text:
                    subtype = "GRAND_TOTAL"
            elif has_product:
                subtype = "LINE_TOTAL"

            amount["amount_subtype"] = subtype
            classified_amounts.append(amount)

    # If there are unclassified amounts, use position heuristics
    unclassified = [a for a in classified_amounts if a.get("amount_subtype") is None]
    if unclassified:
        # Bottom-most unclassified amount is likely GRAND_TOTAL
        bottom_amount = max(
            unclassified,
            key=lambda w: w.get("bbox", {}).get("y", 0)
        )
        bottom_amount["amount_subtype"] = "GRAND_TOTAL"

        # Remaining unclassified default to LINE_TOTAL
        for amount in unclassified:
            if amount.get("amount_subtype") is None:
                amount["amount_subtype"] = "LINE_TOTAL"

    return words


def detect_quantities(
    words: List[Dict[str, Any]],
    y_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Detect QUANTITY tokens based on position relative to PRODUCT_NAME and AMOUNT.

    QUANTITY is typically:
    - A small integer (1-99)
    - Between PRODUCT_NAME and AMOUNT on the same line
    - Currently unlabeled ("O")

    Args:
        words: List of word dicts (will be modified in place)
        y_threshold: Y threshold for line grouping

    Returns:
        The same list with QUANTITY labels added
    """
    lines = group_words_by_line(words, y_threshold)

    for line in lines:
        # Find PRODUCT_NAME and AMOUNT positions on this line
        product_words = [
            w for w in line
            if w.get("label") == "PRODUCT_NAME"
        ]
        amount_words = [
            w for w in line
            if w.get("label") == "AMOUNT"
        ]

        if not product_words or not amount_words:
            continue

        # Get rightmost product and leftmost amount
        rightmost_product = max(
            product_words,
            key=lambda w: w.get("bbox", {}).get("x", 0) + w.get("bbox", {}).get("w", 0)
        )
        leftmost_amount = min(
            amount_words,
            key=lambda w: w.get("bbox", {}).get("x", 0)
        )

        product_right = (
            rightmost_product.get("bbox", {}).get("x", 0) +
            rightmost_product.get("bbox", {}).get("w", 0)
        )
        amount_left = leftmost_amount.get("bbox", {}).get("x", 0)

        # Look for unlabeled small integers between product and amount
        for word in line:
            word_x = word.get("bbox", {}).get("x", 0)
            word_text = word.get("text", "").strip()
            word_label = word.get("label")

            # Must be unlabeled
            if word_label and word_label != "O":
                continue

            # Must be between product and amount
            if not (product_right < word_x < amount_left):
                continue

            # Must be a small integer (1-99) or quantity pattern
            if _is_quantity(word_text):
                word["label"] = "QUANTITY"
                word["label_source"] = "position_rule"

    return words


def _is_quantity(text: str) -> bool:
    """Check if text looks like a quantity."""
    text = text.strip()

    # Direct integer check (1-99)
    if text.isdigit():
        val = int(text)
        return 1 <= val <= 99

    # Pattern: "2x", "x2", "2@", "@2"
    import re
    if re.match(r"^\d{1,2}[x@]$", text, re.IGNORECASE):
        return True
    if re.match(r"^[x@]\d{1,2}$", text, re.IGNORECASE):
        return True

    return False


def detect_unit_prices(
    words: List[Dict[str, Any]],
    y_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Detect UNIT_PRICE tokens based on position.

    UNIT_PRICE is typically:
    - A price format ($X.XX or X.XX)
    - Between QUANTITY and LINE_TOTAL
    - Or after "@" symbol

    Args:
        words: List of word dicts (will be modified in place)
        y_threshold: Y threshold for line grouping

    Returns:
        The same list with UNIT_PRICE labels added
    """
    lines = group_words_by_line(words, y_threshold)

    for line in lines:
        # Find QUANTITY and LINE_TOTAL on this line
        quantity_words = [w for w in line if w.get("label") == "QUANTITY"]
        line_total_words = [
            w for w in line
            if w.get("label") == "AMOUNT" and w.get("amount_subtype") == "LINE_TOTAL"
        ]

        if not line_total_words:
            continue

        leftmost_total = min(
            line_total_words,
            key=lambda w: w.get("bbox", {}).get("x", 0)
        )
        total_x = leftmost_total.get("bbox", {}).get("x", 0)

        # If we have quantity, look between quantity and line total
        if quantity_words:
            rightmost_qty = max(
                quantity_words,
                key=lambda w: w.get("bbox", {}).get("x", 0) + w.get("bbox", {}).get("w", 0)
            )
            qty_right = (
                rightmost_qty.get("bbox", {}).get("x", 0) +
                rightmost_qty.get("bbox", {}).get("w", 0)
            )

            for word in line:
                word_x = word.get("bbox", {}).get("x", 0)
                word_label = word.get("label")

                # Must be unlabeled or AMOUNT without subtype
                if word_label not in [None, "O", "AMOUNT"]:
                    continue
                if word_label == "AMOUNT" and word.get("amount_subtype"):
                    continue

                # Must be between quantity and line total
                if not (qty_right < word_x < total_x):
                    continue

                # Must look like a price
                if _looks_like_price(word.get("text", "")):
                    word["label"] = "UNIT_PRICE"
                    word["label_source"] = "position_rule"

    return words


def _looks_like_price(text: str) -> bool:
    """Check if text looks like a price."""
    import re
    text = text.strip()
    # Match patterns like $1.99, 1.99, $1,234.56
    return bool(re.match(r"^\$?\d{1,3}(?:,\d{3})*(?:\.\d{2})?$", text))


def process_receipt_words(
    words: List[Dict[str, Any]],
    chroma_labeler: Optional[ChromaLabelLookup] = None,
    amount_label: str = "AMOUNT",
    y_threshold: float = 15.0,
) -> List[Dict[str, Any]]:
    """Full post-processing pipeline for receipt words after LayoutLM.

    This function orchestrates the complete post-processing:
    1. Detect line item region from AMOUNT positions
    2. Use ChromaDB to identify PRODUCT_NAME for unlabeled words
    3. Classify AMOUNT subtypes (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL)
    4. Detect QUANTITY based on position rules
    5. Detect UNIT_PRICE based on position rules

    Args:
        words: List of word dicts from LayoutLM inference
            Each dict should have: text, bbox, label, confidence
        chroma_labeler: Optional ChromaDB labeler for PRODUCT_NAME lookup
        amount_label: Label name for amounts from LayoutLM
        y_threshold: Y threshold for line grouping

    Returns:
        Enhanced word list with:
        - PRODUCT_NAME from ChromaDB
        - amount_subtype for AMOUNT tokens
        - QUANTITY from position rules
        - UNIT_PRICE from position rules
        - label_source indicating origin of each label
    """
    # Make a copy to avoid modifying original
    words = [w.copy() for w in words]

    # Step 1: Add label_source for LayoutLM labels
    for word in words:
        if word.get("label") and word["label"] != "O":
            word["label_source"] = word.get("label_source", "layoutlm")

    # Step 2: Detect line item region
    region = detect_line_item_region(words, amount_label, y_threshold)
    if not region:
        logger.warning("No AMOUNT tokens found, skipping post-processing")
        return words

    logger.info(
        "Detected line item region: y_min=%.1f, y_max=%.1f, lines=%d",
        region.y_min, region.y_max, region.line_count
    )

    # Step 3: Use ChromaDB for ALL unlabeled words in region (if labeler provided)
    if chroma_labeler:
        words = _apply_chroma_labels(
            words, chroma_labeler, amount_label, y_threshold
        )

    # Step 4: Classify AMOUNT subtypes using arithmetic
    # This uses: sum(LINE_TOTAL) ≈ SUBTOTAL, SUBTOTAL + TAX ≈ GRAND_TOTAL
    words = classify_amounts_by_arithmetic(words, amount_label, y_threshold)

    # Step 5: Detect QUANTITY (position-based)
    words = detect_quantities(words, y_threshold)

    # Step 6: Detect UNIT_PRICE (position-based)
    words = detect_unit_prices(words, y_threshold)

    return words


def _apply_chroma_labels(
    words: List[Dict[str, Any]],
    chroma_labeler: ChromaLabelLookup,
    amount_label: str,
    y_threshold: float,
) -> List[Dict[str, Any]]:
    """Apply ChromaDB labels to ALL unlabeled words in line item region.

    This queries ChromaDB for every unlabeled word in the line item region,
    not just potential PRODUCT_NAME candidates. ChromaDB can identify:
    - PRODUCT_NAME (semantic similarity to known products)
    - QUANTITY (if labeled in training data)
    - DISCOUNT, COUPON, etc.
    """
    # Detect line item region
    region = detect_line_item_region(words, amount_label, y_threshold)
    if not region:
        return words

    # Find ALL unlabeled words in the line item region
    candidates = []
    for word in words:
        word_y = word.get("bbox", {}).get("y", 0)
        word_label = word.get("label")
        word_text = word.get("text", "").strip()

        # Must be in line item region
        if not (region.y_min - y_threshold <= word_y <= region.y_max + y_threshold):
            continue

        # Must be unlabeled
        if word_label and word_label != "O":
            continue

        # Skip empty or very short text
        if not word_text or len(word_text) < 2:
            continue

        # Skip pure numbers (handled by position rules)
        if word_text.replace(".", "").replace(",", "").replace("$", "").isdigit():
            continue

        candidates.append(word)

    if not candidates:
        return words

    # Batch query ChromaDB for all candidates
    candidate_texts = [w.get("text", "") for w in candidates]
    matches = chroma_labeler.batch_lookup(candidate_texts)

    # Apply labels
    for word, match in zip(candidates, matches):
        if match.predicted_label:
            word["label"] = match.predicted_label
            word["confidence"] = match.confidence
            word["label_source"] = "chroma"

    return words


def _parse_amount(text: str) -> Optional[float]:
    """Parse a price/amount string into a float."""
    if not text:
        return None
    # Remove currency symbols and whitespace
    cleaned = text.strip().replace("$", "").replace(",", "")
    # Handle negative amounts (discounts)
    is_negative = cleaned.startswith("-") or cleaned.startswith("(")
    cleaned = cleaned.replace("-", "").replace("(", "").replace(")", "")
    try:
        value = float(cleaned)
        return -value if is_negative else value
    except ValueError:
        return None


def classify_amounts_by_arithmetic(
    words: List[Dict[str, Any]],
    amount_label: str = "AMOUNT",
    y_threshold: float = 15.0,
    tolerance: float = 0.02,  # 2 cents tolerance for rounding
) -> List[Dict[str, Any]]:
    """Classify AMOUNT tokens using arithmetic relationships.

    Uses the mathematical relationships between receipt amounts:
    - sum(LINE_TOTAL) ≈ SUBTOTAL
    - SUBTOTAL + TAX ≈ GRAND_TOTAL
    - SUBTOTAL - DISCOUNT + TAX ≈ GRAND_TOTAL

    Args:
        words: List of word dicts with AMOUNT labels
        amount_label: Label name for amounts
        y_threshold: Y threshold for line grouping
        tolerance: Tolerance for arithmetic matching (default 2 cents)

    Returns:
        Words with amount_subtype populated based on arithmetic
    """
    # Group words by line
    lines = group_words_by_line(words, y_threshold)

    # Separate line item amounts from totals section amounts
    line_item_amounts: List[Dict[str, Any]] = []
    totals_amounts: List[Dict[str, Any]] = []

    for line in lines:
        line_amounts = [w for w in line if w.get("label") == amount_label]
        if not line_amounts:
            continue

        # Check if this line has a PRODUCT_NAME (indicates line item)
        has_product = any(
            w.get("label") == "PRODUCT_NAME"
            for w in line
        )

        if has_product:
            # Rightmost amount on product line is LINE_TOTAL
            rightmost = max(line_amounts, key=lambda w: w.get("bbox", {}).get("x", 0))
            rightmost["amount_subtype"] = "LINE_TOTAL"
            line_item_amounts.append(rightmost)

            # Other amounts on the line might be UNIT_PRICE
            for amt in line_amounts:
                if amt is not rightmost and not amt.get("amount_subtype"):
                    amt["amount_subtype"] = "UNIT_PRICE"
        else:
            # These are totals section amounts
            totals_amounts.extend(line_amounts)

    # Parse all line totals
    line_total_values = []
    for amt in line_item_amounts:
        value = _parse_amount(amt.get("text", ""))
        if value is not None:
            line_total_values.append(value)
            amt["parsed_value"] = value

    line_items_sum = sum(line_total_values) if line_total_values else 0

    # Parse totals section amounts
    for amt in totals_amounts:
        value = _parse_amount(amt.get("text", ""))
        if value is not None:
            amt["parsed_value"] = value

    # Sort totals by Y position (top to bottom)
    totals_amounts.sort(key=lambda w: w.get("bbox", {}).get("y", 0))

    # Try to identify SUBTOTAL, TAX, GRAND_TOTAL using arithmetic
    identified = _identify_totals_by_arithmetic(
        totals_amounts, line_items_sum, tolerance
    )

    # Apply keyword-based classification for any remaining unclassified
    for amt in totals_amounts:
        if amt.get("amount_subtype"):
            continue

        # Get line text for keyword matching
        amt_y = amt.get("bbox", {}).get("y", 0)
        line_words = [
            w for w in words
            if abs(w.get("bbox", {}).get("y", 0) - amt_y) <= y_threshold
        ]
        line_text = " ".join(w.get("text", "").lower() for w in line_words)

        # Keyword matching as fallback
        if any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["SUBTOTAL"]):
            amt["amount_subtype"] = "SUBTOTAL"
        elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["TAX"]):
            amt["amount_subtype"] = "TAX"
        elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["DISCOUNT"]):
            amt["amount_subtype"] = "DISCOUNT"
        elif any(kw in line_text for kw in AMOUNT_SUBTYPE_KEYWORDS["GRAND_TOTAL"]):
            if "sub" not in line_text:
                amt["amount_subtype"] = "GRAND_TOTAL"

    # Bottom-most unclassified is likely GRAND_TOTAL
    unclassified = [a for a in totals_amounts if not a.get("amount_subtype")]
    if unclassified:
        bottom = max(unclassified, key=lambda w: w.get("bbox", {}).get("y", 0))
        bottom["amount_subtype"] = "GRAND_TOTAL"

    return words


def _identify_totals_by_arithmetic(
    totals: List[Dict[str, Any]],
    line_items_sum: float,
    tolerance: float,
) -> bool:
    """Try to identify totals using arithmetic relationships.

    Tries combinations to find:
    - SUBTOTAL ≈ sum(LINE_TOTAL)
    - GRAND_TOTAL ≈ SUBTOTAL + TAX
    - GRAND_TOTAL ≈ SUBTOTAL - DISCOUNT + TAX

    Returns True if successful identification was made.
    """
    if not totals or line_items_sum == 0:
        return False

    # Get parsed values
    amounts_with_values = [
        (t, t.get("parsed_value"))
        for t in totals
        if t.get("parsed_value") is not None
    ]

    if len(amounts_with_values) < 2:
        return False

    # Find SUBTOTAL: should equal sum of line items
    subtotal_candidate = None
    for amt, value in amounts_with_values:
        if abs(value - line_items_sum) <= tolerance:
            amt["amount_subtype"] = "SUBTOTAL"
            amt["arithmetic_match"] = f"sum(LINE_TOTAL)={line_items_sum:.2f}"
            subtotal_candidate = (amt, value)
            break

    # Find GRAND_TOTAL: largest value, usually at bottom
    amounts_by_value = sorted(amounts_with_values, key=lambda x: x[1], reverse=True)
    grand_total_candidate = None
    for amt, value in amounts_by_value:
        if not amt.get("amount_subtype"):
            amt["amount_subtype"] = "GRAND_TOTAL"
            grand_total_candidate = (amt, value)
            break

    # Try to find TAX: GRAND_TOTAL - SUBTOTAL ≈ TAX
    if subtotal_candidate and grand_total_candidate:
        expected_tax = grand_total_candidate[1] - subtotal_candidate[1]
        if expected_tax > 0:
            for amt, value in amounts_with_values:
                if amt.get("amount_subtype"):
                    continue
                if abs(value - expected_tax) <= tolerance:
                    amt["amount_subtype"] = "TAX"
                    amt["arithmetic_match"] = f"GRAND_TOTAL-SUBTOTAL={expected_tax:.2f}"
                    break

    # Check for DISCOUNT: negative amount or SUBTOTAL - DISCOUNT + TAX = GRAND_TOTAL
    for amt, value in amounts_with_values:
        if amt.get("amount_subtype"):
            continue
        if value < 0:
            amt["amount_subtype"] = "DISCOUNT"
            continue

    return subtotal_candidate is not None or grand_total_candidate is not None
