"""Minimal dual client wrapper for lines/words collections."""

from __future__ import annotations

from typing import Any

from receipt_chroma.data.chroma_client import ChromaClient


class DualChromaClient:
    """Minimal dual client wrapper for lines/words collections."""

    def __init__(self, lines: ChromaClient, words: ChromaClient, logger: Any) -> None:
        self.lines = lines
        self.words = words
        self.logger = logger

    def get_collection(self, collection_name: str, **kwargs):
        """Get collection by name from appropriate client."""
        if collection_name == "lines":
            return self.lines.get_collection("lines", **kwargs)
        if collection_name == "words":
            return self.words.get_collection("words", **kwargs)
        raise ValueError(f"Unknown collection: {collection_name}")

    def query(self, collection_name: str, **kwargs):
        """Query collection by name using appropriate client wrapper."""
        if collection_name == "lines":
            return self.lines.query(collection_name=collection_name, **kwargs)
        if collection_name == "words":
            return self.words.query(collection_name=collection_name, **kwargs)
        raise ValueError(f"Unknown collection: {collection_name}")

    def close(self) -> None:
        """Close both clients safely."""
        try:
            self.lines.close()
        finally:
            try:
                self.words.close()
            except Exception as error:  # pylint: disable=broad-except
                self.logger.debug(
                    "Failed to close words client during cleanup",
                    error=str(error),
                )
