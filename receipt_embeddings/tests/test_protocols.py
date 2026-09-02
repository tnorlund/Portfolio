"""Duck-typed client Protocols match the fakes already used in tests."""

from __future__ import annotations

from types import SimpleNamespace

from receipt_embeddings.protocols import (
    ChromaQueryClient,
    DynamoBatchClient,
    DynamoQueryWriteClient,
    DynamoVectorLowLevelClient,
    EmbeddingTableHandle,
)
from receipt_embeddings.write_requests import build_embedding_write_requests


class _BatchFake:
    def batch_get_item(self, **_kwargs: object) -> dict[str, object]:
        return {"Responses": {}}

    def batch_write_item(self, **_kwargs: object) -> dict[str, object]:
        return {}


class _QueryWriteFake:
    def query(self, **_kwargs: object) -> dict[str, object]:
        return {"Items": []}

    def batch_write_item(self, **_kwargs: object) -> dict[str, object]:
        return {}


class _VectorFake:
    def search_vectors(self, **_kwargs: object) -> dict[str, object]:
        return {"SearchResults": []}

    def get_item(self, **_kwargs: object) -> dict[str, object]:
        return {}

    def batch_get_item(self, **_kwargs: object) -> dict[str, object]:
        return {"Responses": {}}


class _ChromaFake:
    def query(self, **_kwargs: object) -> dict[str, object]:
        return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, **_kwargs: object) -> dict[str, object]:
        return {"embeddings": []}


def test_batch_query_vector_and_chroma_fakes_satisfy_protocols() -> None:
    assert isinstance(_BatchFake(), DynamoBatchClient)
    assert isinstance(_QueryWriteFake(), DynamoQueryWriteClient)
    assert isinstance(_VectorFake(), DynamoVectorLowLevelClient)
    assert isinstance(_ChromaFake(), ChromaQueryClient)


def test_table_handle_protocol_matches_dual_write_shape() -> None:
    handle = SimpleNamespace(_client=_BatchFake(), table_name="t")
    assert isinstance(handle, EmbeddingTableHandle)


def test_write_requests_accept_simple_namespace_lines_and_words() -> None:
    requests = build_embedding_write_requests(
        image_id="img",
        receipt_id=1,
        lines=[SimpleNamespace(line_id=1, text="MILK")],
        words=[SimpleNamespace(line_id=1, word_id=1, text="MILK")],
        word_labels=[
            SimpleNamespace(line_id=1, word_id=1, validation_status="VALID")
        ],
        row_line_ids_list=[[1]],
        include_embedding_input=False,
    )
    assert [item.kind for item in requests] == ["line", "word"]
    assert requests[1].label_status == "validated"
