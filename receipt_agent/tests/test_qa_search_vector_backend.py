"""Vector-seam selection, degradation, and cap tests for the QA tools.

The QA agent's three semantic line searches (search_receipts semantic,
semantic_search, search_product_lines semantic) retrieve through the
shared ``VectorSearchClient`` seam behind ``VECTOR_BACKEND``. The QA
agent must never hard-fail: an unavailable or throwing backend degrades
to empty results with a logged reason. Text/label modes keep their
direct Chroma behavior (their rewrite is explicitly out of this card).
"""

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from receipt_agent.agents.question_answering.tools.search import (
    create_qa_tools,
)

from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.service_limits import LINE_INDEX, MAX_SEARCH_RESULTS
from receipt_embeddings.testing import FakeVectorIndex
from receipt_embeddings.vector_client import VectorItem


class _RecordingClient:
    """Protocol stub that records calls and can fail on demand."""

    def __init__(
        self,
        results: Optional[list] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    def search(self, vector, index, top_k, filters=None):
        del vector, filters
        self.calls.append((index, top_k))
        if self.error is not None:
            raise self.error
        return self.results

    def get_vector(self, key):
        raise KeyError(key)


def _embed_fn(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def _tools_by_name(
    vector_client: Any = None, chroma_client: Any = None
) -> dict[str, Any]:
    tools, _ = create_qa_tools(
        dynamo_client=MagicMock(),
        chroma_client=chroma_client,
        embed_fn=_embed_fn,
        vector_client=vector_client,
    )
    return {tool.name: tool for tool in tools}


def _line_item(
    key: str,
    vector: list[float],
    **metadata: Any,
) -> VectorItem:
    return VectorItem(
        key=key, index=LINE_INDEX, vector=vector, metadata=metadata
    )


def _seeded_index() -> FakeVectorIndex:
    return FakeVectorIndex(
        [
            _line_item(
                "IMAGE#img-1#RECEIPT#00001#LINE#00001",
                [1.0, 0.0],
                image_id="img-1",
                receipt_id=1,
                text="ORGANIC COFFEE 12.99",
                merchant_name="Sprouts",
                section_type="ITEMS",
            ),
            _line_item(
                "IMAGE#img-2#RECEIPT#00002#LINE#00003",
                [0.9, 0.1],
                image_id="img-2",
                receipt_id=2,
                text="FRENCH ROAST 8.49",
                merchant_name="Costco",
                section_label="ITEMS",
            ),
            _line_item(
                "IMAGE#img-3#RECEIPT#00003#LINE#00009",
                [0.95, 0.05],
                image_id="img-3",
                receipt_id=3,
                text="TOTAL 21.48",
                merchant_name="Costco",
                section_label="TOTAL_LINE",
            ),
        ]
    )


def test_injected_client_serves_search_receipts_semantic() -> None:
    tools = _tools_by_name(vector_client=_seeded_index())

    result = tools["search_receipts"].invoke(
        {"query": "coffee", "search_type": "semantic", "auto_fetch": 0}
    )

    assert "error" not in result
    assert result["search_type"] == "semantic"
    assert result["total_matches"] == 3
    ids = {r["image_id"] for r in result["results"]}
    assert ids == {"img-1", "img-2", "img-3"}
    for row in result["results"]:
        assert "similarity_distance" in row


def test_injected_client_serves_semantic_search_tool() -> None:
    tools = _tools_by_name(vector_client=_seeded_index())

    result = tools["semantic_search"].invoke(
        {"query": "coffee", "min_similarity": 0.3}
    )

    assert "error" not in result
    assert result["total_matches"] == 3
    best = result["results"][0]
    assert best["image_id"] == "img-1"
    assert best["similarity"] == 1.0


def test_product_lines_semantic_post_filters_non_item_sections() -> None:
    tools = _tools_by_name(vector_client=_seeded_index())

    result = tools["search_product_lines"].invoke(
        {"query": "coffee", "search_type": "semantic"}
    )

    assert "error" not in result
    texts = {item["text"] for item in result["items"]}
    assert "ORGANIC COFFEE 12.99" in texts
    assert "FRENCH ROAST 8.49" in texts
    # TOTAL_LINE is one of the non-item sections Chroma excluded with
    # $nin inside the ANN query; the port excludes it after retrieval.
    assert "TOTAL 21.48" not in texts


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_top_k"),
    [
        (
            "search_receipts",
            {"query": "q", "search_type": "semantic", "limit": 300},
            MAX_SEARCH_RESULTS,
        ),
        ("semantic_search", {"query": "q", "limit": 300}, MAX_SEARCH_RESULTS),
        (
            "search_product_lines",
            {"query": "q", "search_type": "semantic", "limit": 300},
            MAX_SEARCH_RESULTS,
        ),
        (
            "search_receipts",
            {"query": "q", "search_type": "semantic", "limit": 10},
            20,
        ),
    ],
)
def test_semantic_depth_is_trimmed_to_search_vectors_cap(
    tool_name: str, arguments: dict, expected_top_k: int
) -> None:
    client = _RecordingClient()
    tools = _tools_by_name(vector_client=client)

    result = tools[tool_name].invoke(arguments)

    assert "error" not in result
    assert client.calls == [(LINE_INDEX, expected_top_k)]


def test_unbuildable_backend_degrades_to_empty_results(monkeypatch) -> None:
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    # object() has no .query, so the default Chroma adapter refuses it.
    tools = _tools_by_name(chroma_client=object())

    result = tools["search_receipts"].invoke(
        {"query": "coffee", "search_type": "semantic"}
    )

    assert result["results"] == []
    assert result["total_matches"] == 0
    assert "unavailable" in result["note"]


def test_search_error_degrades_to_empty_results() -> None:
    client = _RecordingClient(error=RuntimeError("throttled"))
    tools = _tools_by_name(vector_client=client)

    for tool_name, arguments in [
        ("search_receipts", {"query": "q", "search_type": "semantic"}),
        ("semantic_search", {"query": "q"}),
        ("search_product_lines", {"query": "q", "search_type": "semantic"}),
    ]:
        result = tools[tool_name].invoke(arguments)
        assert result.get("results", result.get("items")) == []
        assert "error" not in result


def test_text_mode_keeps_direct_chroma_and_skips_the_seam() -> None:
    collection = MagicMock()
    collection.get.return_value = {
        "ids": ["id-1"],
        "metadatas": [
            {"image_id": "img-1", "receipt_id": 1, "text": "COFFEE 3.99"}
        ],
    }
    chroma_client = MagicMock()
    chroma_client.get_collection.return_value = collection
    seam = _RecordingClient()
    tools = _tools_by_name(vector_client=seam, chroma_client=chroma_client)

    result = tools["search_receipts"].invoke(
        {"query": "coffee", "search_type": "text", "auto_fetch": 0}
    )

    assert "error" not in result
    assert result["total_matches"] == 1
    assert seam.calls == []
    chroma_client.get_collection.assert_called_once_with("lines")


def test_dynamo_backend_threads_session_table(monkeypatch) -> None:
    """E3 review P1-3: the seam must receive the session's configured
    table/client instead of falling back to environment defaults."""
    import receipt_agent.agents.question_answering.tools.search as search_mod

    captured: dict[str, Any] = {}

    def _capture(chroma_client, **kwargs):
        del chroma_client
        captured.update(kwargs)
        return _RecordingClient()

    monkeypatch.setattr(search_mod, "vector_search_client", _capture)
    sentinel_boto = object()
    dynamo = MagicMock()
    dynamo.table_name = "ReceiptsTable-session"
    dynamo._client = sentinel_boto

    tools, _ = create_qa_tools(
        dynamo_client=dynamo, chroma_client=None, embed_fn=_embed_fn
    )
    {tool.name: tool for tool in tools}["semantic_search"].invoke(
        {"query": "q"}
    )

    assert captured["table_name"] == "ReceiptsTable-session"
    assert captured["dynamodb_client"] is sentinel_boto


class _FakeDynamoBackend(DynamoVectorSearchClient):
    """isinstance-compatible stub; skips the real constructor."""

    def __init__(self, results):  # pylint: disable=super-init-not-called
        self.results = results

    def search(self, vector, index, top_k, filters=None):
        del vector, index, top_k, filters
        return self.results

    def get_vector(self, key):
        raise KeyError(key)


def test_has_price_label_is_unknown_under_dynamo_backend() -> None:
    """E3 review P2-5: Dynamo line metadata never carries the Chroma
    label_LINE_TOTAL flag, so reporting False would be wrong evidence —
    the flag must read "unknown" until hydrated."""
    neighbor = SimpleNamespace(
        key="IMAGE#img-1#RECEIPT#00001#LINE#00001",
        distance=0.1,
        metadata={
            "image_id": "img-1",
            "receipt_id": 1,
            "text": "RAW MILK 5.99",
            "merchant_name": "Sprouts",
        },
    )
    tools = _tools_by_name(vector_client=_FakeDynamoBackend([neighbor]))

    result = tools["search_product_lines"].invoke(
        {"query": "milk", "search_type": "semantic"}
    )

    assert result["items"][0]["has_price_label"] == "unknown"


def test_has_price_label_keeps_chroma_semantics_off_dynamo() -> None:
    tools = _tools_by_name(vector_client=_seeded_index())

    result = tools["search_product_lines"].invoke(
        {"query": "milk", "search_type": "semantic"}
    )

    assert all(item["has_price_label"] is False for item in result["items"])
