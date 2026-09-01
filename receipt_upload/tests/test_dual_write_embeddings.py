"""Dual-run ingest tests (SPEC §3.4 wiring).

Covers the DUAL_WRITE_EMBEDDINGS gate, reuse of the ingest step's
in-memory vectors (zero OpenAI calls), non-fatal writer failures, and
independence from the Chroma pipeline legs.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

from receipt_upload.merchant_resolution.dynamo_embedding_write import (
    DUAL_WRITE_ENV_VAR,
    build_ingest_embedding_requests,
    dual_write_embeddings_enabled,
    maybe_dual_write_embeddings,
)

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _line(line_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, text=text)


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        extracted_data={},
    )


def _label(line_id: int, word_id: int, status: str) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id, word_id=word_id, validation_status=status
    )


def _vector(seed: float) -> list:
    return [seed] * EMBEDDING_DIMENSIONS


class RecordingWriter:
    """Fake engine writer: records requests, returns a benign report."""

    def __init__(self) -> None:
        self.calls: list = []

    def write(self, requests):
        self.calls.append(list(requests))
        return SimpleNamespace(
            written=len(requests), skipped_existing_keys=[], failures=[]
        )


@pytest.mark.unit
class TestBuildIngestEmbeddingRequests:
    def test_vectors_and_metadata_map_onto_requests(self) -> None:
        lines = [_line(1, "STORE"), _line(2, "123 Main")]
        words = [_word(1, 1, "STORE"), _word(2, 1, "123")]
        row_vectors = [_vector(0.1), _vector(0.2)]
        word_vectors = [_vector(0.3), _vector(0.4)]

        requests = build_ingest_embedding_requests(
            image_id=IMAGE_ID,
            receipt_id=1,
            lines=lines,
            words=words,
            word_labels=[_label(1, 1, "VALID")],
            merchant_name="Cafe Nero",
            place_id="place-1",
            row_embeddings=row_vectors,
            row_line_ids_list=[[1], [2]],
            word_embeddings_list=word_vectors,
        )

        line_requests = [r for r in requests if r.kind == "line"]
        word_requests = [r for r in requests if r.kind == "word"]
        assert len(line_requests) == 2 and len(word_requests) == 2

        first = line_requests[0]
        assert first.line_id == 1
        assert first.text == "STORE"
        assert first.vector == row_vectors[0]
        assert first.row_line_ids == (1,)
        assert first.section_type == ""
        assert first.merchant_name == "Cafe Nero"
        assert first.place_id == "place-1"

        by_word = {(r.line_id, r.word_id): r for r in word_requests}
        assert by_word[(1, 1)].label_status == "validated"
        assert by_word[(1, 1)].vector == word_vectors[0]
        assert by_word[(2, 1)].label_status == "none"

    def test_multi_line_visual_row_uses_primary_line(self) -> None:
        lines = [_line(1, "STORE"), _line(2, "#42")]
        requests = build_ingest_embedding_requests(
            image_id=IMAGE_ID,
            receipt_id=1,
            lines=lines,
            words=[],
            word_labels=[],
            merchant_name="",
            place_id="",
            row_embeddings=[_vector(0.5)],
            row_line_ids_list=[[1, 2]],
            word_embeddings_list=[],
        )
        assert len(requests) == 1
        assert requests[0].line_id == 1
        assert requests[0].row_line_ids == (1, 2)
        assert requests[0].text == "STORE #42"


@pytest.mark.unit
class TestMaybeDualWriteEmbeddings:
    def _kwargs(self, **overrides):
        dynamo = MagicMock()
        dynamo.table_name = "test-table"
        base = {
            "dynamo": dynamo,
            "image_id": IMAGE_ID,
            "receipt_id": 1,
            "lines": [_line(1, "STORE")],
            "words": [_word(1, 1, "STORE")],
            "word_labels": [],
            "receipt_place": None,
            "row_embeddings": [_vector(0.1)],
            "row_line_ids_list": [[1]],
            "word_embeddings_list": [_vector(0.2)],
        }
        base.update(overrides)
        return base

    def test_flag_off_means_zero_writer_calls(self, monkeypatch) -> None:
        monkeypatch.delenv(DUAL_WRITE_ENV_VAR, raising=False)
        factory = MagicMock()
        assert not dual_write_embeddings_enabled()
        result = maybe_dual_write_embeddings(
            writer_factory=factory, **self._kwargs()
        )
        assert result is None
        factory.assert_not_called()

    def test_flag_on_invokes_writer_with_in_memory_vectors(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(DUAL_WRITE_ENV_VAR, "true")
        writer = RecordingWriter()
        kwargs = self._kwargs()
        result = maybe_dual_write_embeddings(
            writer_factory=lambda client, table: writer, **kwargs
        )
        assert result == {
            "enabled": True,
            "requests": 2,
            "written": 2,
            "skipped_existing": 0,
            "failed": 0,
        }
        (requests,) = writer.calls
        # Every request carries a pre-computed vector, so the engine
        # writer never reaches for OpenAI.
        assert all(r.vector is not None for r in requests)
        assert requests[0].vector == kwargs["row_embeddings"][0]
        assert requests[1].vector == kwargs["word_embeddings_list"][0]

    def test_place_metadata_flows_from_receipt_place(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(DUAL_WRITE_ENV_VAR, "true")
        writer = RecordingWriter()
        place = SimpleNamespace(merchant_name="Costco", place_id="p-9")
        maybe_dual_write_embeddings(
            writer_factory=lambda client, table: writer,
            **self._kwargs(receipt_place=place),
        )
        (requests,) = writer.calls
        assert requests[0].merchant_name == "Costco"
        assert requests[0].place_id == "p-9"

    def test_writer_failure_never_raises(self, monkeypatch) -> None:
        monkeypatch.setenv(DUAL_WRITE_ENV_VAR, "true")
        failing = MagicMock()
        failing.write.side_effect = RuntimeError("dynamo down")
        result = maybe_dual_write_embeddings(
            writer_factory=lambda client, table: failing, **self._kwargs()
        )
        assert result["error"] == "dynamo down"
        assert result["written"] == 0


@pytest.mark.unit
class TestProcessorWiringIndependence:
    """The dual write runs even when the Chroma pipeline legs fail."""

    def test_dual_write_called_when_chroma_pipelines_fail(
        self, monkeypatch
    ) -> None:
        from receipt_upload.merchant_resolution import (
            embedding_processor as ep,
        )

        # The impl imports OpenAI lazily; a stub keeps the test offline.
        monkeypatch.setitem(
            sys.modules,
            "openai",
            types.SimpleNamespace(OpenAI=lambda: object()),
        )
        row_embeddings = [_vector(0.1)]
        row_line_ids_list = [[1]]
        word_embeddings_list = [_vector(0.2)]
        dual_report = {"enabled": True, "written": 2, "failed": 0}

        with patch.object(ep, "DynamoClient") as mock_dynamo, patch.object(
            ep, "boto3"
        ), patch.object(ep, "MerchantResolver"), patch.object(
            ep,
            "download_and_embed_parallel",
            return_value=(
                None,
                None,
                row_embeddings,
                row_line_ids_list,
                word_embeddings_list,
            ),
        ), patch.object(
            ep, "log_merchant_resolution"
        ), patch.object(
            ep,
            "maybe_dual_write_embeddings",
            return_value=dual_report,
        ) as dual_mock:
            client = MagicMock()
            client.table_name = "test-table"
            client.list_receipt_word_labels_for_receipt.return_value = (
                [],
                None,
            )
            client.get_receipt_place.return_value = None
            mock_dynamo.return_value = client

            processor = ep.MerchantResolvingEmbeddingProcessor(
                table_name="test-table", chromadb_bucket="bucket"
            )
            # SimpleNamespace entities make Phase 2's dataclass
            # serialization blow up — the Chroma legs fail wholesale.
            result = processor._process_embeddings_impl(
                image_id=IMAGE_ID,
                receipt_id=1,
                lines=[_line(1, "STORE")],
                words=[_word(1, 1, "STORE")],
            )

        # Chroma leg failed (no compaction run) …
        assert result["success"] is False
        # … but the dual write ran with the very vectors Phase 1 produced.
        dual_mock.assert_called_once()
        call_kwargs = dual_mock.call_args.kwargs
        assert call_kwargs["row_embeddings"] is row_embeddings
        assert call_kwargs["word_embeddings_list"] is word_embeddings_list
        assert result["dual_write"] is dual_report
