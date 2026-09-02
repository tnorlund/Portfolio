"""Cross-server tests for the similarity tool surface (card E3).

Both MCP servers (stdio ``scripts/receipt_mcp_server.py`` and the
vendored Lambda fork) must:

- register ``similar_labeled_words`` with the same schema and serve the
  spec §3.7 search-then-join through it,
- retire ``validate_word_similarity`` to a deprecation pointer that
  never touches Chroma,
- serve the SEMANTIC modes of search_receipts / search_product_lines
  through the VectorSearchClient seam, trimmed to the 100-result
  SearchVectors cap, and
- with VECTOR_BACKEND=dynamodb, allow the semantic modes to proceed
  without Chroma credentials while text modes keep the structured
  chroma_not_configured error.
"""

import asyncio
import inspect
import json
import sys
import types
from types import SimpleNamespace

import pytest
from test_receipt_mcp_section_tools import SERVER_FILES, _load_module

from receipt_embeddings.dynamo_client import DynamoVectorSearchClient

IMG = "3f2a1b0c-4d5e-4f70-8192-a3b4c5d6e7f8"
NEIGHBOR_IMG = "9e8d7c6b-5a49-4382-a1b0-c9d8e7f6a5b4"

SIMILARITY_FIELDS = {
    "image_id",
    "receipt_id",
    "line_id",
    "word_id",
    "label",
}


def _neighbor(
    image_id,
    receipt_id,
    text,
    *,
    key="",
    distance=0.1,
    **extra,
):
    metadata = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "text": text,
        "merchant_name": "Sprouts",
        **extra,
    }
    return SimpleNamespace(key=key, distance=distance, metadata=metadata)


class _StubVectorClient:
    """Protocol stub recording searches; neighbors are configurable."""

    def __init__(self, results=None, vector_error=None):
        self.results = results or []
        self.vector_error = vector_error
        self.calls = []

    def get_vector(self, key):
        if self.vector_error is not None:
            raise self.vector_error
        return [1.0, 0.0]

    def search(self, vector, index, top_k, filters=None):
        del vector
        self.calls.append((index, top_k, dict(filters) if filters else None))
        return self.results


class _StubDynamo:
    """Serves the merchant lookup and the label-row batch join."""

    def get_receipt_place(self, image_id, receipt_id):
        del image_id, receipt_id
        return SimpleNamespace(merchant_name="Sprouts")

    def get_receipt_word_labels(self, keys):
        rows = []
        for image_id, receipt_id, line_id, word_id, label in keys:
            if label == "GRAND_TOTAL":
                rows.append(
                    SimpleNamespace(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line_id,
                        word_id=word_id,
                        label=label,
                        validation_status="VALID",
                        reasoning="follows TOTAL keyword",
                        label_proposed_by="human",
                        timestamp_added="2026-08-31T00:00:00+00:00",
                    )
                )
        return rows


# ---------------------------------------------------------------------------
# Registration / schema parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_similar_labeled_words_registered_with_valid_schema(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = {t.name: t for t in asyncio.run(module.list_tools())}

    assert "similar_labeled_words" in tools
    schema = tools["similar_labeled_words"].inputSchema
    assert schema["type"] == "object"
    assert set(schema["required"]) == SIMILARITY_FIELDS
    assert set(schema["properties"]) == SIMILARITY_FIELDS


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_validate_word_similarity_is_marked_deprecated(label):
    module = _load_module(label, SERVER_FILES[label])
    tools = {t.name: t for t in asyncio.run(module.list_tools())}

    description = tools["validate_word_similarity"].description
    assert description.startswith("[DEPRECATED]")
    assert "similar_labeled_words" in description


def test_both_servers_agree_on_similarity_surface():
    stdio = _load_module("stdio", SERVER_FILES["stdio"])
    lam = _load_module("lambda", SERVER_FILES["lambda"])

    stdio_tools = {t.name: t for t in asyncio.run(stdio.list_tools())}
    lam_tools = {t.name: t for t in asyncio.run(lam.list_tools())}
    for name in ("similar_labeled_words", "validate_word_similarity"):
        assert stdio_tools[name].inputSchema == lam_tools[name].inputSchema
        assert stdio_tools[name].description == lam_tools[name].description

    # The vendored fork must carry byte-equal similarity plumbing.
    for function_name in (
        "similar_labeled_words_impl",
        "validate_word_similarity_impl",
        "search_receipts_impl",
        "search_product_lines_impl",
        "get_vector_search_client",
        "_vector_backend",
    ):
        assert inspect.getsource(
            getattr(stdio, function_name)
        ) == inspect.getsource(getattr(lam, function_name)), function_name


# ---------------------------------------------------------------------------
# validate_word_similarity: deprecation pointer, no Chroma traffic
# ---------------------------------------------------------------------------


class _ExplodingChroma:
    def __getattr__(self, name):
        raise AssertionError(
            f"retired tool must not touch Chroma (accessed {name!r})"
        )


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_validate_word_similarity_returns_deprecation_pointer(label):
    module = _load_module(label, SERVER_FILES[label])

    result = asyncio.run(
        module.validate_word_similarity_impl(
            _ExplodingChroma(),
            image_id=IMG,
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="GRAND_TOTAL",
        )
    )

    assert result["deprecated"] is True
    assert result["error_type"] == "deprecated_tool"
    assert result["replacement"] == "similar_labeled_words"
    assert result["word"]["label"] == "GRAND_TOTAL"


# ---------------------------------------------------------------------------
# similar_labeled_words: search-then-join through the seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_similar_labeled_words_joins_label_rows(label):
    module = _load_module(label, SERVER_FILES[label])
    client = _StubVectorClient(
        results=[
            _neighbor(
                NEIGHBOR_IMG,
                1,
                "3.94",
                key=(
                    f"IMAGE#{NEIGHBOR_IMG}#RECEIPT#00001"
                    "#LINE#00001#WORD#00001"
                ),
                line_id=1,
                word_id=1,
            )
        ]
    )

    result = asyncio.run(
        module.similar_labeled_words_impl(
            _StubDynamo(),
            image_id=IMG,
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="GRAND_TOTAL",
            vector_client=client,
        )
    )

    assert result["found_vector"] is True
    assert client.calls == [
        ("word-embeddings", 25, {"label_status": "validated"})
    ]
    assert len(result["evidence_for"]) == 1
    evidence = result["evidence_for"][0]
    assert evidence["reasoning"] == "follows TOTAL keyword"
    assert evidence["proposed_by"] == "human"
    assert evidence["same_merchant"] is True
    assert result["evidence_against"] == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_similar_labeled_words_answers_gracefully_without_vector(label):
    module = _load_module(label, SERVER_FILES[label])
    client = _StubVectorClient(vector_error=KeyError("missing"))

    result = asyncio.run(
        module.similar_labeled_words_impl(
            _StubDynamo(),
            image_id=IMG,
            receipt_id=1,
            line_id=2,
            word_id=3,
            label="GRAND_TOTAL",
            vector_client=client,
        )
    )

    assert result["found_vector"] is False
    assert "No stored vector" in result["reason"]
    assert result["evidence_for"] == []
    assert "error" not in result


# ---------------------------------------------------------------------------
# Semantic search modes through the seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_search_receipts_semantic_uses_seam_and_caps_depth(label):
    module = _load_module(label, SERVER_FILES[label])
    stub = _StubVectorClient(
        results=[_neighbor(IMG, 1, "ORGANIC COFFEE 12.99", distance=0.2)]
    )

    result = asyncio.run(
        module.search_receipts_impl(
            None,
            lambda texts: [[1.0, 0.0] for _ in texts],
            query="coffee",
            search_type="semantic",
            limit=300,
            vector_client=stub,
        )
    )

    assert "error" not in result
    # limit*2 = 600 must be trimmed to the 100-result SearchVectors cap.
    assert stub.calls == [("line-embeddings", 100, None)]
    assert result["results"][0]["image_id"] == IMG
    assert result["results"][0]["similarity"] == 0.8


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_search_product_lines_semantic_post_filters_sections(label):
    module = _load_module(label, SERVER_FILES[label])
    stub = _StubVectorClient(
        results=[
            _neighbor(IMG, 1, "RAW MILK 5.99", distance=0.1),
            _neighbor(
                IMG,
                1,
                "TOTAL 21.48",
                distance=0.1,
                section_label="TOTAL_LINE",
            ),
        ]
    )

    result = asyncio.run(
        module.search_product_lines_impl(
            None,
            lambda texts: [[1.0, 0.0] for _ in texts],
            query="milk",
            search_type="semantic",
            limit=10,
            vector_client=stub,
        )
    )

    assert "error" not in result
    texts = {item["text"] for item in result["items"]}
    # TOTAL_LINE is one of the sections Chroma excluded with $nin inside
    # the ANN query; the seam port excludes it after retrieval.
    assert texts == {"RAW MILK 5.99"}
    assert result["raw_total"] == 5.99


# ---------------------------------------------------------------------------
# Dispatch: VECTOR_BACKEND=dynamodb without Chroma credentials
# ---------------------------------------------------------------------------


def _install_stub_embed_factory(monkeypatch):
    factory_mod = types.ModuleType("receipt_agent.clients.factory")
    factory_mod.create_embed_fn = lambda: (
        lambda texts: [[1.0, 0.0] for _ in texts]
    )
    monkeypatch.setitem(
        sys.modules, "receipt_agent.clients.factory", factory_mod
    )


def _tool_result(module, name, arguments):
    content = asyncio.run(module.call_tool(name, arguments))
    assert content, "call_tool returned no content"
    return json.loads(content[0].text)


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamodb_backend_serves_semantic_without_chroma(label, monkeypatch):
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    _install_stub_embed_factory(monkeypatch)
    module.get_dynamo_client = lambda: SimpleNamespace()

    def _no_chroma():
        raise module.ChromaNotConfiguredError("no chroma configured")

    module.get_chroma_clients = _no_chroma
    stub = _StubVectorClient(
        results=[_neighbor(IMG, 1, "ORGANIC COFFEE 12.99", distance=0.2)]
    )
    module.get_vector_search_client = lambda chroma_client=None: stub

    result = _tool_result(
        module,
        "search_receipts",
        {"query": "coffee", "search_type": "semantic"},
    )

    assert "error" not in result
    assert result["results"][0]["image_id"] == IMG


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_text_mode_unavailable_under_dynamodb_backend(label, monkeypatch):
    """Chroma teardown: the retired Chroma-only modes answer with a
    structured "unavailable" result instead of raising — and the server
    must never even attempt to build a Chroma client on this backend."""
    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    _install_stub_embed_factory(monkeypatch)
    module.get_dynamo_client = lambda: SimpleNamespace()

    def _no_chroma():
        raise AssertionError(
            "get_chroma_clients must not be called on dynamodb backend"
        )

    module.get_chroma_clients = _no_chroma

    result = _tool_result(
        module,
        "search_receipts",
        {"query": "coffee", "search_type": "text"},
    )

    assert "unavailable on the dynamodb backend" in result["error"]
    assert result["search_type"] == "text"
    assert result["results"] == []


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_dynamodb_backend_threads_session_table(label, monkeypatch):
    """E3 review P1-3: get_vector_search_client must hand the session's
    configured table/client to the seam, never the env fallback."""
    import receipt_embeddings.backend as backend_mod

    module = _load_module(label, SERVER_FILES[label])
    monkeypatch.setenv("VECTOR_BACKEND", "dynamodb")
    sentinel_boto = object()
    module.get_dynamo_client = lambda: SimpleNamespace(
        _client=sentinel_boto, table_name="ReceiptsTable-session"
    )
    captured = {}

    def _capture(chroma_client, **kwargs):
        del chroma_client
        captured.update(kwargs)
        return _StubVectorClient()

    monkeypatch.setattr(backend_mod, "vector_search_client", _capture)

    module.get_vector_search_client()

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


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_has_price_label_is_unknown_under_dynamo_backend(label):
    """E3 review P2-5: Dynamo line metadata never carries the Chroma
    label_LINE_TOTAL flag — report "unknown", not a false False."""
    module = _load_module(label, SERVER_FILES[label])
    fake = _FakeDynamoBackend(
        [_neighbor(IMG, 1, "RAW MILK 5.99", distance=0.1)]
    )

    result = asyncio.run(
        module.search_product_lines_impl(
            None,
            lambda texts: [[1.0, 0.0] for _ in texts],
            query="milk",
            search_type="semantic",
            limit=10,
            vector_client=fake,
        )
    )

    assert result["items"][0]["has_price_label"] == "unknown"


@pytest.mark.parametrize("label", sorted(SERVER_FILES))
def test_has_price_label_keeps_chroma_semantics_off_dynamo(label):
    module = _load_module(label, SERVER_FILES[label])
    stub = _StubVectorClient(
        results=[_neighbor(IMG, 1, "RAW MILK 5.99", distance=0.1)]
    )

    result = asyncio.run(
        module.search_product_lines_impl(
            None,
            lambda texts: [[1.0, 0.0] for _ in texts],
            query="milk",
            search_type="semantic",
            limit=10,
            vector_client=stub,
        )
    )

    assert result["items"][0]["has_price_label"] is False
