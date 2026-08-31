"""Unit tests for the embed-and-put writer against an in-memory Dynamo."""

from dataclasses import dataclass, field

import pytest

from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from receipt_dynamo.entities.receipt_line_embedding import (
    EMBEDDING_DIMENSIONS,
)
from receipt_embeddings.writer import (
    SKIP_MISSING_VECTOR,
    SKIP_WRITE_FAILED,
    EmbedAndPutWriter,
    EmbeddingRequest,
    OpenAIVectorSource,
    line_embedding_key,
    word_embedding_key,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
RECEIPT_ID = 1


@dataclass
class FakeGeometry:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict
    word_id: int = 0

    def calculate_centroid(self):
        return (
            self.bounding_box["x"] + self.bounding_box["width"] / 2,
            self.bounding_box["y"] + self.bounding_box["height"] / 2,
        )


def make_line(line_id: int, text: str, y: float) -> FakeGeometry:
    return FakeGeometry(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        line_id=line_id,
        text=text,
        bounding_box={"x": 0.1, "y": y, "width": 0.8, "height": 0.03},
    )


def make_word(line_id: int, word_id: int, text: str, y: float, x: float):
    return FakeGeometry(
        image_id=IMAGE_ID,
        receipt_id=RECEIPT_ID,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": 0.15, "height": 0.03},
    )


@dataclass
class FakeLabel:
    line_id: int
    word_id: int
    label: str
    validation_status: str
    timestamp_added: str = "2026-08-30T00:00:00"


@dataclass
class FakeDetails:
    lines: list
    words: list
    labels: list


@dataclass
class FakePlace:
    merchant_name: str = "Costco"
    place_id: str = "ChIJexample"


class FakeDynamo:
    """The accessor surface EmbedAndPutWriter needs, in memory."""

    def __init__(self, details: FakeDetails, place: FakePlace | None):
        self.details = details
        self.place = place
        self.line_embeddings: dict[int, object] = {}
        self.word_embeddings: dict[tuple[int, int], object] = {}
        self.batch_calls = 0
        self.fail_line_batch = False

    def get_receipt_details(self, image_id, receipt_id):
        return self.details

    def get_receipt_place(self, image_id, receipt_id):
        if self.place is None:
            raise EntityNotFoundError("no place")
        return self.place

    def get_receipt_sections_from_receipt(self, image_id, receipt_id):
        return []

    def list_receipt_line_embeddings_from_receipt(self, image_id, receipt_id):
        return list(self.line_embeddings.values())

    def list_receipt_word_embeddings_from_receipt(self, image_id, receipt_id):
        return list(self.word_embeddings.values())

    def add_receipt_line_embeddings(self, embeddings):
        self.batch_calls += 1
        if self.fail_line_batch:
            raise RuntimeError("batch exploded")
        for embedding in embeddings:
            self.line_embeddings[embedding.line_id] = embedding

    def add_receipt_line_embedding(self, embedding):
        if embedding.line_id in self.line_embeddings:
            raise EntityAlreadyExistsError("exists")
        self.line_embeddings[embedding.line_id] = embedding

    def add_receipt_word_embeddings(self, embeddings):
        self.batch_calls += 1
        for embedding in embeddings:
            self.word_embeddings[(embedding.line_id, embedding.word_id)] = (
                embedding
            )

    def add_receipt_word_embedding(self, embedding):
        key = (embedding.line_id, embedding.word_id)
        if key in self.word_embeddings:
            raise EntityAlreadyExistsError("exists")
        self.word_embeddings[key] = embedding


class RecordingVectorSource:
    """Returns deterministic vectors; optionally omits some keys."""

    def __init__(self, omit: set[str] = frozenset()):
        self.requests: list[EmbeddingRequest] = []
        self.calls = 0
        self.omit = set(omit)

    def vectors_for(self, requests):
        self.calls += 1
        self.requests.extend(requests)
        vectors = {}
        for position, request in enumerate(requests):
            if request.key in self.omit:
                continue
            vector = [0.0] * EMBEDDING_DIMENSIONS
            vector[position % EMBEDDING_DIMENSIONS] = 1.0
            vectors[request.key] = vector
        return vectors


@pytest.fixture
def receipt_details():
    lines = [
        make_line(1, "COSTCO WHOLESALE", 0.9),
        make_line(2, "TOTAL", 0.5),
        make_line(3, "5.99", 0.5),  # same visual row as line 2
    ]
    words = [
        make_word(1, 1, "COSTCO", 0.9, 0.1),
        make_word(1, 2, "WHOLESALE", 0.9, 0.4),
        make_word(2, 1, "TOTAL", 0.5, 0.1),
        make_word(3, 1, "5.99", 0.5, 0.7),
    ]
    labels = [
        FakeLabel(2, 1, "GRAND_TOTAL", "VALID"),
        FakeLabel(3, 1, "AMOUNT", "PENDING"),
    ]
    return FakeDetails(lines=lines, words=words, labels=labels)


@pytest.mark.unit
def test_embed_receipt_writes_rows_and_words(receipt_details):
    dynamo = FakeDynamo(receipt_details, FakePlace())
    source = RecordingVectorSource()
    writer = EmbedAndPutWriter(dynamo, vector_source=source)

    report = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)

    # Two visual rows (line 1; lines 2+3 merged) and four words.
    assert sorted(dynamo.line_embeddings) == [1, 2]
    assert len(dynamo.word_embeddings) == 4
    assert report.written_count == 6
    assert report.skipped_existing_count == 0
    assert report.failures == []

    row = dynamo.line_embeddings[2]
    assert row.row_line_ids == [2, 3]
    assert row.text == "TOTAL 5.99"
    assert row.merchant_name == "Costco"
    assert row.place_id == "ChIJexample"

    total_word = dynamo.word_embeddings[(2, 1)]
    assert total_word.label_status == "validated"
    assert total_word.primary_label == "GRAND_TOTAL"
    assert total_word.valid_labels == ["GRAND_TOTAL"]
    amount_word = dynamo.word_embeddings[(3, 1)]
    assert amount_word.label_status == "pending"
    unlabeled_word = dynamo.word_embeddings[(1, 1)]
    assert unlabeled_word.label_status == "none"

    # The row embedding input carries above/below context with <EDGE>.
    row_inputs = {
        request.key: request.input_text for request in source.requests
    }
    assert (
        row_inputs[line_embedding_key(IMAGE_ID, RECEIPT_ID, 1)]
        == "<EDGE>\nCOSTCO WHOLESALE\nTOTAL 5.99"
    )


@pytest.mark.unit
def test_embed_receipt_is_idempotent(receipt_details):
    dynamo = FakeDynamo(receipt_details, FakePlace())
    source = RecordingVectorSource()
    writer = EmbedAndPutWriter(dynamo, vector_source=source)

    first = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)
    assert first.written_count == 6
    batch_calls_after_first = dynamo.batch_calls

    second = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)

    # Re-run writes nothing, embeds nothing, and reports every item as
    # already existing.
    assert second.written_count == 0
    assert second.skipped_existing_count == 6
    assert second.failures == []
    assert dynamo.batch_calls == batch_calls_after_first
    assert source.calls == 1  # no second vector-source call


@pytest.mark.unit
def test_embed_receipt_missing_vector_skips_and_reports(receipt_details):
    missing_key = word_embedding_key(IMAGE_ID, RECEIPT_ID, 1, 2)
    dynamo = FakeDynamo(receipt_details, FakePlace())
    writer = EmbedAndPutWriter(
        dynamo, vector_source=RecordingVectorSource(omit={missing_key})
    )

    report = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)

    assert report.written_count == 5
    assert [failure.key for failure in report.failures] == [missing_key]
    assert report.failures[0].reason == SKIP_MISSING_VECTOR
    assert (1, 2) not in dynamo.word_embeddings


@pytest.mark.unit
def test_embed_receipt_without_place_writes_sparse_items(receipt_details):
    dynamo = FakeDynamo(receipt_details, place=None)
    writer = EmbedAndPutWriter(dynamo, vector_source=RecordingVectorSource())

    report = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)

    assert report.failures == []
    assert dynamo.line_embeddings[1].merchant_name is None
    assert dynamo.line_embeddings[1].place_id is None


@pytest.mark.unit
def test_embed_receipt_batch_failure_degrades_per_item(receipt_details):
    dynamo = FakeDynamo(receipt_details, FakePlace())
    dynamo.fail_line_batch = True
    writer = EmbedAndPutWriter(dynamo, vector_source=RecordingVectorSource())

    report = writer.embed_receipt(IMAGE_ID, RECEIPT_ID)

    # Line batch exploded; every line landed via the per-item fallback,
    # and the word batch was never aborted.
    assert sorted(dynamo.line_embeddings) == [1, 2]
    assert len(dynamo.word_embeddings) == 4
    assert report.written_count == 6
    assert not any(
        failure.reason == SKIP_WRITE_FAILED for failure in report.failures
    )


@pytest.mark.unit
def test_embed_receipt_empty_receipt_raises(receipt_details):
    dynamo = FakeDynamo(
        FakeDetails(lines=[], words=[], labels=[]), FakePlace()
    )
    writer = EmbedAndPutWriter(dynamo, vector_source=RecordingVectorSource())
    with pytest.raises(ValueError, match="no lines or words"):
        writer.embed_receipt(IMAGE_ID, RECEIPT_ID)


@pytest.mark.unit
def test_openai_vector_source_batches_and_orders():
    embedded_batches = []

    class FakeOpenAI:
        class embeddings:  # noqa: N801 - mimic OpenAI client surface
            @staticmethod
            def create(model, input):  # noqa: A002
                embedded_batches.append(list(input))
                return type(
                    "Response",
                    (),
                    {
                        "data": [
                            type("Datum", (), {"embedding": [float(i)]})()
                            for i, _ in enumerate(input)
                        ]
                    },
                )()

    source = OpenAIVectorSource(openai_client=FakeOpenAI(), batch_size=2)
    requests = [
        EmbeddingRequest(key=f"key-{i}", input_text=f"text {i}")
        for i in range(5)
    ]
    vectors = source.vectors_for(requests)

    assert [len(batch) for batch in embedded_batches] == [2, 2, 1]
    assert vectors["key-0"] == [0.0]
    assert vectors["key-4"] == [0.0]
    assert len(vectors) == 5
