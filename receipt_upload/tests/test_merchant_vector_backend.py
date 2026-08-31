"""Vector-backend switch and graceful degradation for merchant retrieval."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from receipt_dynamo.entities import ReceiptLine
from receipt_embeddings import ScoredItem

from receipt_upload.merchant_resolution.resolver import MerchantResolver


class StubVectorClient:
    def __init__(
        self,
        results: list[ScoredItem] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(
        self,
        _vector: list[float],
        index: str,
        top_k: int,
        _filters: Any = None,
    ) -> list[ScoredItem]:
        self.calls.append((index, top_k))
        if self.error is not None:
            raise self.error
        return self.results

    def get_vector(self, _key: str) -> list[float]:
        raise KeyError


def _line() -> MagicMock:
    line = MagicMock(spec=ReceiptLine, line_id=1, text="Fixture Mart")
    line.calculate_centroid.return_value = (0.5, 0.9)
    return line


@pytest.mark.unit
def test_dynamo_backend_uses_protocol_and_preserves_resolution_math() -> None:
    vector_client = StubVectorClient(
        [
            ScoredItem(
                key="neighbor",
                distance=0.1,
                metadata={
                    "image_id": "other-image",
                    "receipt_id": 9,
                    "merchant_name": "Fixture Mart",
                    "normalized_phone_10": "5551234567",
                },
            )
        ]
    )
    dynamo = MagicMock()
    dynamo.get_receipt_place.return_value = SimplePlace(
        "place-1", "Fixture Mart"
    )
    resolver = MerchantResolver(
        dynamo_client=dynamo,
        vector_client=vector_client,
        vector_backend="dynamodb",
    )
    resolver._line_embeddings = {1: [0.01] * 1536}
    resolver._receipt_lines = [_line()]

    result = resolver._similarity_search_impl(
        lines_client=MagicMock(),
        query_line=_line(),
        current_image_id="current-image",
        current_receipt_id=1,
        expected_phone="5551234567",
        expected_address=None,
        resolution_tier="chroma_phone",
    )

    assert vector_client.calls == [("line-embeddings", 20)]
    assert result.place_id == "place-1"
    assert result.confidence == pytest.approx(1.0)


@pytest.mark.unit
def test_dynamo_throttle_degrades_to_empty_result() -> None:
    resolver = MerchantResolver(
        dynamo_client=MagicMock(),
        vector_client=StubVectorClient(error=RuntimeError("throttled")),
        vector_backend="dynamodb",
    )
    resolver._line_embeddings = {1: [0.01] * 1536}
    resolver._receipt_lines = [_line()]

    result = resolver._similarity_search_impl(
        lines_client=MagicMock(),
        query_line=_line(),
        current_image_id="current-image",
        current_receipt_id=1,
        expected_phone=None,
        expected_address=None,
        resolution_tier="chroma_text",
    )

    assert result.place_id is None


@pytest.mark.unit
def test_invalid_vector_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="VECTOR_BACKEND"):
        MerchantResolver(MagicMock(), vector_backend="production")


@pytest.mark.unit
def test_vector_backend_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")

    resolver = MerchantResolver(
        dynamo_client=MagicMock(), vector_client=StubVectorClient()
    )

    assert resolver._vector_backend == "dynamodb"


class SimplePlace:
    def __init__(self, place_id: str, merchant_name: str) -> None:
        self.place_id = place_id
        self.merchant_name = merchant_name
