"""Base classes for label validators."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from receipt_dynamo.entities import ReceiptWord, ReceiptWordLabel

from receipt_label.label_validation.data import LabelValidationResult
from receipt_label.utils.client_manager import ClientManager


@dataclass
class ValidatorConfig:
    """Configuration for a validator."""

    similarity_threshold: float = 0.7
    n_neighbors: int = 10
    requires_receipt_metadata: bool = False
    requires_merchant_name: bool = False
    enable_llm_validation: bool = False  # Enable LLM-based validation
    use_chroma_examples: bool = True  # Include ChromaDB neighbors in LLM prompt


class BaseLabelValidator(ABC):
    """Base class for all label validators."""

    def __init__(self, config: Optional[ValidatorConfig] = None):
        self.config = config or ValidatorConfig()

    @abstractmethod
    def validate(
        self,
        word: ReceiptWord,
        label: ReceiptWordLabel,
        client_manager: Optional[ClientManager] = None,
        **kwargs,
    ) -> LabelValidationResult:
        """
        Validate a label.

        Args:
            word: The word being validated
            label: The label to validate
            client_manager: Client manager for database access
            **kwargs: Additional context (receipt_metadata, merchant_name, etc.)

        Returns:
            LabelValidationResult with validation outcome
        """
        pass

    @property
    @abstractmethod
    def supported_labels(self) -> list[str]:
        """Return list of label types this validator supports."""
        pass

    def get_chroma_neighbors(
        self,
        word: ReceiptWord,
        label: ReceiptWordLabel,
        client_manager: ClientManager,
    ) -> list[dict]:
        """
        Get similar examples from ChromaDB for context.

        Returns:
            List of neighbor examples with metadata
        """
        if not client_manager or not self.config.use_chroma_examples:
            return []

        try:
            from receipt_label.label_validation.utils import chroma_id_from_label

            chroma_id = chroma_id_from_label(label)
            chroma_client = client_manager.chroma

            # Get vector from ChromaDB
            results = chroma_client.get_by_ids(
                "words", [chroma_id], include=["embeddings", "metadatas"]
            )

            if not results or "ids" not in results or len(results["ids"]) == 0:
                return []

            idx = results["ids"].index(chroma_id) if chroma_id in results["ids"] else -1
            if idx < 0:
                return []

            vector = results["embeddings"][idx] if "embeddings" in results else None
            if not vector:
                return []

            # Query for similar vectors
            query_results = chroma_client.query_collection(
                collection_name="words",
                query_embeddings=[vector],
                n_results=self.config.n_neighbors,
                where={"valid_labels": {"$in": [label.label]}},
                include=["metadatas", "distances"],
            )

            neighbors = []
            if query_results and "ids" in query_results and len(query_results["ids"]) > 0:
                for i, neighbor_id in enumerate(query_results["ids"][0]):
                    neighbor_metadata = (
                        query_results["metadatas"][0][i]
                        if "metadatas" in query_results
                        else {}
                    )
                    distance = (
                        query_results["distances"][0][i]
                        if "distances" in query_results
                        else 1.0
                    )
                    neighbors.append(
                        {
                            "id": neighbor_id,
                            "word_text": neighbor_metadata.get("word_text", ""),
                            "merchant_name": neighbor_metadata.get("merchant_name", ""),
                            "similarity_score": 1.0 - distance,
                            "metadata": neighbor_metadata,
                        }
                    )

            return neighbors
        except Exception:
            # Fail silently - neighbors are optional
            return []

