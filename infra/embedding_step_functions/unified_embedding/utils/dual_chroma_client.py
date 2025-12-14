from __future__ import annotations

import logging
from typing import Any

from receipt_chroma.data.chroma_client import ChromaClient


class DualChromaClient:
    """Minimal dual client wrapper for lines/words collections."""

    def __init__(
        self, lines: ChromaClient, words: ChromaClient, logger: Any
    ) -> None:
        self.lines = lines
        self.words = words
        self.logger = logger

    def get_collection(self, collection_name: str, **kwargs):
        if collection_name == "lines":
            return self.lines.get_collection("lines", **kwargs)
        if collection_name == "words":
            return self.words.get_collection("words", **kwargs)
        raise ValueError(f"Unknown collection: {collection_name}")

    def query(self, collection_name: str, **kwargs):
        return self.get_collection(collection_name).query(**kwargs)

    def close(self) -> None:
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
