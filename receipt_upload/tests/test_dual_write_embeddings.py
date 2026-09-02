"""Native embedding writer tests (Chroma teardown).

Covers the ingest request builder, the unconditional precomputed-vector
write (``write_precomputed_embeddings``), the processor wiring that makes
the native write THE persistence step, and the standalone batched writer
(``write_native_embeddings``) used by the correction flows.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from receipt_dynamo.entities import EMBEDDING_DIMENSIONS

from receipt_upload.merchant_resolution.dynamo_embedding_write import (
    build_ingest_embedding_requests,
    write_precomputed_embeddings,
)

IMAGE_ID = "2f1e7204-84f1-4ab3-9b05-7dc6edebc1b7"


def _line(line_id: int, text: str) -> SimpleNamespace:
    # Real geometry so group_lines_into_visual_rows can row-group:
    # each line gets its own non-overlapping vertical band.
    return SimpleNamespace(
        line_id=line_id,
        text=text,
        bounding_box={
            "x": 0.1,
            "y": 1.0 - 0.1 * line_id,
            "width": 0.8,
            "height": 0.05,
        },
    )


def _word(line_id: int, word_id: int, text: str) -> SimpleNamespace:
    centroid = (0.1 + 0.1 * word_id, 1.0 - 0.1 * line_id)
    return SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        extracted_data={},
        calculate_centroid=lambda: centroid,
        bounding_box={
            "x": 0.1 + 0.1 * word_id,
            "y": 1.0 - 0.1 * line_id,
            "width": 0.08,
            "height": 0.05,
        },
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

    def test_blank_rows_and_words_are_skipped(self) -> None:
        """The engine writer refuses empty text, so blank OCR rows and
        words must never become requests (they'd surface as failures)."""
        requests = build_ingest_embedding_requests(
            image_id=IMAGE_ID,
            receipt_id=1,
            lines=[_line(1, "STORE"), _line(2, "  ")],
            words=[_word(1, 1, "STORE"), _word(2, 1, "")],
            word_labels=[],
            merchant_name="",
            place_id="",
            row_embeddings=[_vector(0.1), _vector(0.2)],
            row_line_ids_list=[[1], [2]],
            word_embeddings_list=[_vector(0.3), _vector(0.4)],
        )
        assert len([r for r in requests if r.kind == "line"]) == 1
        assert len([r for r in requests if r.kind == "word"]) == 1


@pytest.mark.unit
class TestWritePrecomputedEmbeddings:
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

    def test_writes_with_in_memory_vectors(self) -> None:
        writer = RecordingWriter()
        kwargs = self._kwargs()
        result = write_precomputed_embeddings(
            writer_factory=lambda client, table: writer, **kwargs
        )
        assert result == {
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

    def test_place_metadata_flows_from_receipt_place(self) -> None:
        writer = RecordingWriter()
        place = SimpleNamespace(merchant_name="Costco", place_id="p-9")
        write_precomputed_embeddings(
            writer_factory=lambda client, table: writer,
            **self._kwargs(receipt_place=place),
        )
        (requests,) = writer.calls
        assert requests[0].merchant_name == "Costco"
        assert requests[0].place_id == "p-9"

    def test_writer_failure_never_raises(self) -> None:
        failing = MagicMock()
        failing.write.side_effect = RuntimeError("dynamo down")
        result = write_precomputed_embeddings(
            writer_factory=lambda client, table: failing, **self._kwargs()
        )
        assert result["error"] == "dynamo down"
        assert result["written"] == 0


@pytest.mark.unit
class TestProcessorWiring:
    """The native write is THE persistence step: it runs on Phase 1's
    vectors and its report decides the receipt's success."""

    def _run_impl(self, native_report):
        from receipt_upload.merchant_resolution import (
            embedding_processor as ep,
        )

        row_embeddings = [_vector(0.1)]
        row_line_ids_list = [[1]]
        word_embeddings_list = [_vector(0.2)]

        with (
            patch.object(ep, "DynamoClient") as mock_dynamo,
            patch.object(ep, "boto3"),
            patch.object(ep, "MerchantResolver"),
            patch.object(
                ep,
                "_embed_receipt_vectors",
                return_value=(
                    row_embeddings,
                    row_line_ids_list,
                    word_embeddings_list,
                ),
            ),
            patch.object(ep, "log_merchant_resolution"),
            patch.object(
                ep,
                "write_precomputed_embeddings",
                return_value=native_report,
            ) as native_mock,
        ):
            client = MagicMock()
            client.table_name = "test-table"
            client.list_receipt_word_labels_for_receipt.return_value = (
                [],
                None,
            )
            client.get_receipt_place.return_value = None
            mock_dynamo.return_value = client

            processor = ep.MerchantResolvingEmbeddingProcessor(
                table_name="test-table"
            )
            # SimpleNamespace entities make Phase 2's dataclass
            # serialization blow up — both pipeline legs fail wholesale.
            result = processor._process_embeddings_impl(
                image_id=IMAGE_ID,
                receipt_id=1,
                lines=[_line(1, "STORE")],
                words=[_word(1, 1, "STORE")],
            )
        return result, native_mock, row_embeddings, word_embeddings_list

    def test_native_write_decides_success_even_when_pipelines_fail(
        self,
    ) -> None:
        native_report = {"requests": 2, "written": 2, "failed": 0}
        result, native_mock, rows, words_v = self._run_impl(native_report)

        # Phase-2 pipelines failed (SimpleNamespace serialization), but
        # the corpus write succeeded — the receipt is searchable, so the
        # run succeeds.
        assert result["success"] is True
        native_mock.assert_called_once()
        call_kwargs = native_mock.call_args.kwargs
        assert call_kwargs["row_embeddings"] is rows
        assert call_kwargs["word_embeddings_list"] is words_v
        assert result["native_embeddings"] is native_report

    def test_incomplete_native_write_fails_the_receipt(self) -> None:
        native_report = {"requests": 2, "written": 1, "failed": 1}
        result, _mock, _r, _w = self._run_impl(native_report)
        assert result["success"] is False
        assert result["native_embeddings"] is native_report


@pytest.mark.unit
class TestWriteNativeEmbeddings:
    """Standalone batched writer for correction flows: embeds the
    ingest-formatted inputs and persists via the engine writer."""

    def _run(self, monkeypatch, *, sweep=False, raw=None):
        import receipt_embeddings

        from receipt_upload.merchant_resolution.dynamo_embedding_write import (
            write_native_embeddings,
        )

        writer = RecordingWriter()
        captured = {}

        def fake_embed(client, texts, model="text-embedding-3-small"):
            captured["texts"] = list(texts)
            return [_vector(0.7) for _ in texts]

        monkeypatch.setattr(
            "receipt_embeddings.openai.realtime.embed_texts", fake_embed
        )
        monkeypatch.setattr(
            receipt_embeddings,
            "EmbeddingWriter",
            lambda client, table: writer,
        )
        dynamo = MagicMock()
        dynamo.table_name = "test-table"
        if raw is not None:
            dynamo._client = raw
        result = write_native_embeddings(
            dynamo,
            image_id=IMAGE_ID,
            receipt_id=1,
            lines=[_line(1, "STORE"), _line(2, "")],
            words=[_word(1, 1, "STORE")],
            word_labels=[_label(1, 1, "INVALID")],
            receipt_place=SimpleNamespace(
                merchant_name="Costco", place_id="p-9"
            ),
            sweep_existing=sweep,
        )
        return result, writer, captured

    def test_batch_embeds_and_writes_with_vectors(self, monkeypatch):
        result, writer, captured = self._run(monkeypatch)
        (requests,) = writer.calls
        # blank line 2 skipped; one line row + one word
        assert result["requests"] == len(requests) == 2
        assert len(captured["texts"]) == 2
        assert all(r.vector is not None for r in requests)
        word = [r for r in requests if r.kind == "word"][0]
        # terminal-verdict rule: INVALID-only stays validated
        assert word.label_status == "validated"
        line = [r for r in requests if r.kind == "line"][0]
        assert line.merchant_name == "Costco"
        assert line.place_id == "p-9"
        assert result["swept"] == 0

    def test_sweep_existing_deletes_before_write(self, monkeypatch):
        raw = MagicMock()
        raw.query.return_value = {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{IMAGE_ID}"},
                    "SK": {"S": "RECEIPT#00001#LINE#00001#EMBEDDING"},
                },
                {
                    "PK": {"S": f"IMAGE#{IMAGE_ID}"},
                    "SK": {"S": "RECEIPT#00001#LINE#00001#WORD#00001"},
                },
            ]
        }
        raw.batch_write_item.return_value = {"UnprocessedItems": {}}
        result, _writer, _ = self._run(monkeypatch, sweep=True, raw=raw)
        # only the #EMBEDDING-suffixed key is swept
        assert result["swept"] == 1
        raw.batch_write_item.assert_called_once()

    def test_vector_count_mismatch_raises(self, monkeypatch):
        import receipt_embeddings

        from receipt_upload.merchant_resolution.dynamo_embedding_write import (
            write_native_embeddings,
        )

        monkeypatch.setattr(
            "receipt_embeddings.openai.realtime.embed_texts",
            lambda client, texts, model="m": [],
        )
        monkeypatch.setattr(
            receipt_embeddings,
            "EmbeddingWriter",
            lambda client, table: RecordingWriter(),
        )
        with pytest.raises(RuntimeError, match="returned 0 vectors"):
            write_native_embeddings(
                MagicMock(table_name="t"),
                image_id=IMAGE_ID,
                receipt_id=1,
                lines=[_line(1, "STORE")],
                words=[],
                word_labels=[],
                receipt_place=None,
            )
