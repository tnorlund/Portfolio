"""
Local data loader for development and testing.

This module provides utilities to load exported DynamoDB data from JSON files
and reconstruct proper Receipt, ReceiptLine, and ReceiptWord objects for
local development and testing without accessing DynamoDB.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

# Removed lru_cache import - using instance cache instead


logger = logging.getLogger(__name__)


class LocalDataLoader:
    """Load receipt data from local JSON exports."""

    def __init__(self, data_dir: str):
        """
        Initialize the local data loader.

        Args:
            data_dir: Path to directory containing exported receipt data
        """
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise ValueError(f"Data directory does not exist: {data_dir}")

        # Cache for loaded data - keyed by directory path
        self._receipt_cache: Dict[str, Optional[Receipt]] = {}

    def load_receipt_by_id(
        self, image_id: str, receipt_id: str
    ) -> Optional[Tuple[Receipt, List[ReceiptWord], List[ReceiptLine]]]:
        """
        Load a receipt and its associated entities by ID.

        Args:
            image_id: Image ID containing the receipt
            receipt_id: Receipt ID to load

        Returns:
            Tuple of (Receipt, words, lines) or None if not found
        """
        receipt_dir = self.data_dir / f"image_{image_id}_receipt_{receipt_id}"
        if not receipt_dir.exists():
            logger.warning("Receipt directory not found: %s", receipt_dir)
            return None

        try:
            # Load receipt
            receipt = self._load_receipt(receipt_dir)
            if not receipt:
                return None

            # Load words
            words = self._load_words(receipt_dir)

            # Load lines
            lines = self._load_lines(receipt_dir)

            return receipt, words, lines

        except Exception as e:
            logger.error(
                "Error loading receipt %s/%s: %s", image_id, receipt_id, e
            )
            return None

    def load_receipt_with_labels(
        self, image_id: str, receipt_id: str
    ) -> Optional[
        Tuple[
            Receipt,
            List[ReceiptWord],
            List[ReceiptLine],
            List[ReceiptWordLabel],
        ]
    ]:
        """
        Load a receipt with all entities including labels.

        Args:
            image_id: Image ID containing the receipt
            receipt_id: Receipt ID to load

        Returns:
            Tuple of (Receipt, words, lines, labels) or None if not found
        """
        result = self.load_receipt_by_id(image_id, receipt_id)
        if not result:
            return None

        receipt, words, lines = result

        # Load labels
        receipt_dir = self.data_dir / f"image_{image_id}_receipt_{receipt_id}"
        labels = self._load_labels(receipt_dir)

        return receipt, words, lines, labels

    def load_receipt_metadata(
        self, image_id: str, receipt_id: str
    ) -> Optional[ReceiptMetadata]:
        """
        Load receipt metadata if available.

        Args:
            image_id: Image ID containing the receipt
            receipt_id: Receipt ID

        Returns:
            ReceiptMetadata or None if not found
        """
        receipt_dir = self.data_dir / f"image_{image_id}_receipt_{receipt_id}"
        return self._load_metadata(receipt_dir)
    
    def list_available_receipts(self) -> List[Tuple[str, str]]:
        """
        List all available receipts in the data directory.
        
        Returns:
            List of (image_id, receipt_id) tuples
        """
        receipts = []
        
        # Look for directories matching pattern image_*_receipt_*
        for receipt_dir in self.data_dir.glob("image_*_receipt_*"):
            if receipt_dir.is_dir():
                # Extract image_id and receipt_id from directory name
                parts = receipt_dir.name.split("_")
                if len(parts) >= 4:
                    image_id = parts[1]
                    receipt_id = parts[3]
                    receipts.append((image_id, receipt_id))
        
        return receipts
    
    def load_metadata(self, image_id: str, receipt_id: str) -> Optional[ReceiptMetadata]:
        """Alias for load_receipt_metadata for compatibility."""
        return self.load_receipt_metadata(image_id, receipt_id)

    def _load_receipt(self, receipt_dir: Path) -> Optional[Receipt]:
        """Load receipt entity from JSON with caching."""
        # Use string representation of path as cache key
        cache_key = str(receipt_dir)

        # Check cache first
        if cache_key in self._receipt_cache:
            return self._receipt_cache[cache_key]

        receipt_file = receipt_dir / "receipt.json"
        if not receipt_file.exists():
            logger.warning("Receipt file not found: %s", receipt_file)
            result = None
        else:
            try:
                with open(receipt_file, encoding="utf-8") as f:
                    data = json.load(f)
                # Receipt doesn't have from_dict, create directly from attributes
                result = Receipt(**data)
            except Exception as e:
                logger.error(
                    "Error loading receipt from %s: %s", receipt_file, e
                )
                result = None

        # Cache the result (including None for missing files)
        # Limit cache size to prevent unbounded growth
        if len(self._receipt_cache) >= 100:
            # Remove oldest entry (simple FIFO strategy)
            first_key = next(iter(self._receipt_cache))
            del self._receipt_cache[first_key]

        self._receipt_cache[cache_key] = result
        return result

    def _load_words(self, receipt_dir: Path) -> List[ReceiptWord]:
        """Load receipt words from JSON."""
        words_file = receipt_dir / "words.json"
        if not words_file.exists():
            logger.warning("Words file not found: %s", words_file)
            return []

        try:
            with open(words_file, encoding="utf-8") as f:
                data = json.load(f)
            return [ReceiptWord(**word_data) for word_data in data]
        except Exception as e:
            logger.error("Error loading words from %s: %s", words_file, e)
            return []

    def _load_lines(self, receipt_dir: Path) -> List[ReceiptLine]:
        """Load receipt lines from JSON."""
        lines_file = receipt_dir / "lines.json"
        if not lines_file.exists():
            logger.warning(f"Lines file not found: {lines_file}")
            return []

        try:
            with open(lines_file, encoding="utf-8") as f:
                data = json.load(f)
            return [ReceiptLine(**line_data) for line_data in data]
        except Exception as e:
            logger.error("Error loading lines from %s: %s", lines_file, e)
            return []

    def _load_labels(self, receipt_dir: Path) -> List[ReceiptWordLabel]:
        """Load receipt word labels from JSON."""
        labels_file = receipt_dir / "labels.json"
        if not labels_file.exists():
            logger.debug(f"Labels file not found: {labels_file}")
            return []

        try:
            with open(labels_file, encoding="utf-8") as f:
                data = json.load(f)
            return [ReceiptWordLabel(**label_data) for label_data in data]
        except Exception as e:
            logger.error("Error loading labels from %s: %s", labels_file, e)
            return []

    def _load_metadata(self, receipt_dir: Path) -> Optional[ReceiptMetadata]:
        """Load receipt metadata from JSON."""
        metadata_file = receipt_dir / "metadata.json"
        if not metadata_file.exists():
            logger.debug(f"Metadata file not found: {metadata_file}")
            return None

        try:
            with open(metadata_file, encoding="utf-8") as f:
                data = json.load(f)
            return ReceiptMetadata(**data)
        except Exception as e:
            logger.error(
                "Error loading metadata from %s: %s", metadata_file, e
            )
            return None

    def list_available_receipts(self) -> List[Tuple[str, str]]:
        """
        List all available receipts in the data directory.

        Returns:
            List of (image_id, receipt_id) tuples
        """
        receipts = []

        # Find all receipt directories
        for receipt_dir in self.data_dir.glob("image_*_receipt_*"):
            if receipt_dir.is_dir():
                # Parse image_id and receipt_id from directory name
                parts = receipt_dir.name.split("_")
                if len(parts) >= 4:
                    image_id = parts[1]
                    receipt_id = parts[3]
                    receipts.append((image_id, receipt_id))

        return sorted(receipts)

    def load_sample_index(self) -> Optional[Dict]:
        """
        Load the sample index if available.

        Returns:
            Sample index data or None
        """
        index_file = self.data_dir / "sample_index.json"
        if not index_file.exists():
            return None

        try:
            with open(index_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading sample index: %s", e)
            return None

    def load_master_index(self) -> Optional[Dict]:
        """
        Load the master index of all exported receipts.

        Returns:
            Master index data or None
        """
        index_file = self.data_dir / "index.json"
        if not index_file.exists():
            return None

        try:
            with open(index_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading master index: %s", e)
            return None


def create_mock_receipt_from_export(
    export_data: Dict[str, List[Dict]],
) -> Tuple[Receipt, List[ReceiptWord], List[ReceiptLine]]:
    """
    Create mock Receipt objects from exported JSON data.

    This is a convenience function for tests that need to work with
    raw exported data without going through files.

    Args:
        export_data: Dictionary with 'receipt', 'words', 'lines' keys

    Returns:
        Tuple of (Receipt, words, lines)
    """
    receipt_data = export_data.get("receipt", {})
    receipt = Receipt(**receipt_data) if receipt_data else None

    words_data = export_data.get("words", [])
    words = [ReceiptWord(**w) for w in words_data]

    lines_data = export_data.get("lines", [])
    lines = [ReceiptLine(**l) for l in lines_data]

    return receipt, words, lines
