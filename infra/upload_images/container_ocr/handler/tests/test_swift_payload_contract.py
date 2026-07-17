"""Contract tests for the Swift OCR worker -> process-OCR-results handoff.

The Mac worker (receipt_ocr_swift) uploads one JSON payload per image and the
container Lambda parses it into DynamoDB entities. These tests pin both sides
of that boundary against the shared fixture
``receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/swift_single_pass_contract.json``
(the same file a Swift-side test round-trips), so a schema drift on either
side fails a local test before it fails in the Lambda.

Covered contract points:
  1. Swift single-pass detection (``receipts`` + ``classification`` present)
  2. lines/words/letters parse into Receipt* entities with 1-based ARRAY
     POSITION ids (skipped entries still consume their id — the invariant
     that keeps Swift's LayoutLM word labels aligned with Python's words)
  3. layoutlm_predictions are parallel to the receipt lines/words arrays
  4. Swift's hardcoded core-label vocabulary matches receipt_agent.CORE_LABELS
  5. barcodes parse into ReceiptBarcode entities
  6. OCR-results SQS messages are never misrouted to the LLM-validation path
  7. visual ReceiptRows are persisted by ingest, BEFORE the handler invokes
     the embedding/section pipeline
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_container_ocr_dir = str(Path(__file__).resolve().parents[2])
if _container_ocr_dir not in sys.path:
    sys.path.insert(0, _container_ocr_dir)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_FIXTURE_PATH = (
    _REPO_ROOT
    / "receipt_ocr_swift"
    / "Tests"
    / "ReceiptOCRCoreTests"
    / "Fixtures"
    / "swift_single_pass_contract.json"
)
_SWIFT_LABEL_SOURCE = (
    _REPO_ROOT
    / "receipt_ocr_swift"
    / "Sources"
    / "ReceiptOCRCore"
    / "Models"
    / "ReceiptWordLabel.swift"
)

import boto3
import pytest
from handler.ocr_processor import OCRProcessor
from moto import mock_aws
from receipt_agent.constants import CORE_LABELS
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import OCRJob, OCRRoutingDecision

IMAGE_ID = "12345678-1234-4123-8123-123456789012"
JOB_ID = "87654321-4321-4321-8321-210987654321"


@pytest.fixture(scope="module")
def swift_payload():
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def parser():
    """OCRProcessor instance for the pure parsing methods.

    The ``_parse_*_from_swift`` methods touch no instance state, so skip
    ``__init__`` (which builds a real DynamoClient) entirely.
    """
    return OCRProcessor.__new__(OCRProcessor)


# ---------------------------------------------------------------------------
# 1. Detection: what makes a payload take the Swift single-pass path
# ---------------------------------------------------------------------------


def test_fixture_is_detected_as_swift_single_pass(swift_payload):
    """process_ocr_job routes on non-empty 'receipts' + 'classification'."""
    assert swift_payload.get("receipts")
    assert swift_payload.get("classification")
    # The legacy multi-pass path is taken when either key is missing; both
    # halves of the routing condition must hold for the fixture.
    classification = swift_payload["classification"]
    assert classification["image_type"] in {"NATIVE", "PHOTO", "SCAN"}
    assert classification["image_width"] > 0
    assert classification["image_height"] > 0


# ---------------------------------------------------------------------------
# 2. Receipt (warped-crop) OCR entities
# ---------------------------------------------------------------------------


def test_receipt_lines_words_letters_parse(parser, swift_payload):
    receipt = swift_payload["receipts"][0]
    lines, words, letters = parser._parse_receipt_ocr_from_swift(
        IMAGE_ID, receipt["cluster_id"], receipt["lines"]
    )

    # Fixture has 4 lines; line index 2 has empty text and is skipped, but
    # its line_id stays consumed: ids are 1-based ARRAY POSITIONS.
    assert [ln.line_id for ln in lines] == [1, 3, 4]
    assert lines[0].text == "SPROUTS FARMERS MARKET"

    # Line 3 has an empty-text word at position 2 — skipped, id consumed.
    line3_word_ids = sorted(
        w.word_id for w in words if w.line_id == 3
    )
    assert line3_word_ids == [1, 3]

    # Geometry and confidence survive the mapping.
    first = next(w for w in words if w.line_id == 1 and w.word_id == 1)
    assert first.text == "SPROUTS"
    assert set(first.bounding_box) == {"x", "y", "width", "height"}
    assert 0.0 < first.confidence <= 1.0

    # extracted_data passes through for currency words.
    price = next(w for w in words if w.line_id == 3 and w.word_id == 3)
    assert price.extracted_data == {"type": "currency", "value": "3.99"}

    # Letters only exist for words that shipped them (SPROUTS: 7 letters).
    assert len(letters) == 7
    assert all(
        (letter.line_id, letter.word_id) == (1, 1) for letter in letters
    )


def test_first_pass_lines_parse(parser, swift_payload):
    lines, words, _ = parser._parse_original_ocr_from_swift(
        IMAGE_ID, swift_payload["lines"]
    )
    assert len(lines) == 2
    assert len(words) == 5  # 3 merchant words + TOTAL + amount


# ---------------------------------------------------------------------------
# 3. LayoutLM predictions stay aligned with the words the parser persists
# ---------------------------------------------------------------------------


def test_layoutlm_predictions_parallel_to_lines_and_words(swift_payload):
    """Swift writes ReceiptWordLabels keyed by (line array pos, token pos).

    Python assigns the same 1-based array positions when it persists
    ReceiptWord entities, so labels land on the right words ONLY if the
    predictions array is parallel to `lines` and each `tokens` list is
    parallel to that line's `words` list. Pin that shape.
    """
    receipt = swift_payload["receipts"][0]
    lines = receipt["lines"]
    predictions = receipt["layoutlm_predictions"]

    assert len(predictions) == len(lines)
    for line, prediction in zip(lines, predictions):
        tokens = prediction["tokens"]
        labels = prediction["labels"]
        confidences = prediction["confidences"]
        assert len(tokens) == len(labels) == len(confidences)
        assert len(tokens) == len(line["words"])
        for token, word in zip(tokens, line["words"]):
            assert token == word["text"]
        for label in labels:
            stripped = re.sub(r"^[BI]-", "", label)
            assert stripped == "O" or stripped in CORE_LABELS


def test_swift_core_label_vocabulary_matches_python():
    """Swift hardcodes its own copy of CORE_LABELS; keep the copies equal.

    A label present in Python but missing in Swift is silently dropped by
    ReceiptWordLabel.fromLinePredictions; the reverse would write labels the
    validators do not recognize.
    """
    source = _SWIFT_LABEL_SOURCE.read_text(encoding="utf-8")
    match = re.search(
        r"coreLabels:\s*Set<String>\s*=\s*\[(.*?)\]", source, re.DOTALL
    )
    assert match, "coreLabels set not found in ReceiptWordLabel.swift"
    swift_labels = set(re.findall(r'"([A-Z_]+)"', match.group(1)))
    assert swift_labels == set(CORE_LABELS)


# ---------------------------------------------------------------------------
# 4. Barcodes
# ---------------------------------------------------------------------------


def test_receipt_barcodes_parse(parser, swift_payload):
    receipt = swift_payload["receipts"][0]
    barcodes = parser._parse_receipt_barcodes_from_swift(
        IMAGE_ID, receipt["cluster_id"], receipt["barcodes"]
    )
    assert len(barcodes) == 1
    assert barcodes[0].symbology == "EAN13"
    assert barcodes[0].text == "0123456789012"
    assert 0.0 < barcodes[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# 5. SQS routing: OCR-results messages never take the LLM-validation path
# ---------------------------------------------------------------------------


def test_ocr_results_message_is_not_llm_validation_record():
    from handler.handler import _is_llm_validation_record

    # Exactly what OCRWorker.swift sends after uploading results.
    swift_body = {
        "image_id": IMAGE_ID,
        "job_id": JOB_ID,
        "s3_key": f"ocr_results/{IMAGE_ID}.json",
        "s3_bucket": "raw-bucket",
        "receipt_count": 1,
    }
    record = {"body": json.dumps(swift_body)}
    # Body-shape fallback: job_id present => OCR job, even though s3_key is
    # also present.
    assert _is_llm_validation_record(record) is False
    # ARN routing is authoritative when present.
    record["eventSourceARN"] = "arn:aws:sqs:us-east-1:1:x-ocr-results-queue"
    assert _is_llm_validation_record(record) is False


# ---------------------------------------------------------------------------
# 6. Ingest persists ReceiptRows before the embedding/section pipeline
# ---------------------------------------------------------------------------


_GSI = [
    {
        "IndexName": name,
        "KeySchema": [
            {"AttributeName": f"{name}PK", "KeyType": "HASH"},
            {"AttributeName": f"{name}SK", "KeyType": "RANGE"},
        ],
        "Projection": {"ProjectionType": "ALL"},
        "ProvisionedThroughput": {
            "ReadCapacityUnits": 5,
            "WriteCapacityUnits": 5,
        },
    }
    for name in ("GSI1", "GSI2", "GSI3")
]


@pytest.fixture
def aws_stack():
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")
        table_name = "contract-test-table"
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": key, "AttributeType": "S"}
                for key in (
                    "PK",
                    "SK",
                    "GSI1PK",
                    "GSI1SK",
                    "GSI2PK",
                    "GSI2SK",
                    "GSI3PK",
                    "GSI3SK",
                    "TYPE",
                )
            ],
            GlobalSecondaryIndexes=_GSI
            + [
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [
                        {"AttributeName": "TYPE", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
        )
        dynamodb.get_waiter("table_exists").wait(TableName=table_name)
        s3 = boto3.client("s3", region_name="us-east-1")
        for bucket in ("raw-bucket", "site-bucket"):
            s3.create_bucket(Bucket=bucket)
        yield table_name


def test_swift_single_pass_persists_rows_before_embedding(
    aws_stack, swift_payload
):
    """`_process_swift_single_pass` must leave ReceiptRows in DynamoDB when it
    returns, because the handler starts the embedding/section pipeline only
    after `process_ocr_job` returns and that pipeline reads the persisted rows
    (`get_receipt_rows_from_receipt`) for section assignment."""
    table_name = aws_stack
    now = datetime.now(timezone.utc)
    processor = OCRProcessor(
        table_name=table_name,
        raw_bucket="raw-bucket",
        site_bucket="site-bucket",
        ocr_job_queue_url="https://sqs.test/jobs",
        ocr_results_queue_url="https://sqs.test/results",
    )
    ocr_job = OCRJob(
        image_id=IMAGE_ID,
        job_id=JOB_ID,
        s3_bucket="raw-bucket",
        s3_key=f"images/{IMAGE_ID}.png",
        created_at=now,
        updated_at=now,
    )
    routing = OCRRoutingDecision(
        image_id=IMAGE_ID,
        job_id=JOB_ID,
        s3_bucket="raw-bucket",
        s3_key=f"ocr_results/{IMAGE_ID}.json",
        created_at=now,
        updated_at=now,
        receipt_count=0,
    )
    processor.dynamo.add_ocr_job(ocr_job)
    processor.dynamo.add_ocr_routing_decision(routing)

    result = processor._process_swift_single_pass(
        swift_payload, ocr_job, routing
    )

    assert result["success"] is True
    assert result["swift_single_pass"] is True
    assert result["receipt_ids"] == [1]
    # Per-receipt OCR entities the handler forwards to process_embeddings.
    per_receipt = result["per_receipt_data"][1]
    assert [ln.line_id for ln in per_receipt["lines"]] == [1, 3, 4]

    dynamo = DynamoClient(table_name)
    rows = dynamo.get_receipt_rows_from_receipt(IMAGE_ID, 1)
    assert rows, "ingest must persist visual ReceiptRows synchronously"
    persisted_line_ids = sorted(
        line_id for row in rows for line_id in row.line_ids
    )
    assert persisted_line_ids == [1, 3, 4]
    # Row identity: row_id is the primary (leftmost) line of the visual row —
    # the key the lines pipeline uses to join rows to RowEmbeddingRecords.
    assert all(row.row_id in row.line_ids for row in rows)

    barcodes = dynamo.list_receipt_barcodes_from_receipt(IMAGE_ID, 1)
    assert len(barcodes) == 1


def test_handler_runs_embedding_after_ocr_persistence(monkeypatch):
    """Pipeline ordering at the handler level: process_ocr_job completes
    (receipts + rows persisted) BEFORE MerchantResolvingEmbeddingProcessor
    is constructed and invoked with the per-receipt entities."""
    from handler import handler as handler_module

    for key, value in {
        "DYNAMO_TABLE_NAME": "contract-test-table",
        "RAW_BUCKET": "raw-bucket",
        "SITE_BUCKET": "site-bucket",
        "OCR_JOB_QUEUE_URL": "https://sqs.test/jobs",
        "OCR_RESULTS_QUEUE_URL": "https://sqs.test/results",
        "CHROMADB_BUCKET": "chroma-bucket",
    }.items():
        monkeypatch.setenv(key, value)

    calls = []

    class FakeOCRProcessor:
        def __init__(self, **_kwargs):
            pass

        def process_ocr_job(self, image_id, job_id):
            calls.append("process_ocr_job")
            return {
                "success": True,
                "image_id": image_id,
                "image_type": "PHOTO",
                "receipt_id": 1,
                "receipt_ids": [1],
                "per_receipt_data": {
                    1: {"lines": ["line-sentinel"], "words": ["word-sentinel"]}
                },
                "swift_single_pass": True,
            }

    class FakeEmbeddingProcessor:
        def __init__(self, **_kwargs):
            calls.append("embedding_processor_init")

        def process_embeddings(self, image_id, receipt_id, lines, words):
            calls.append(("process_embeddings", receipt_id, lines, words))
            return {"merchant_found": False}

    monkeypatch.setattr(handler_module, "OCRProcessor", FakeOCRProcessor)
    monkeypatch.setattr(
        handler_module,
        "MerchantResolvingEmbeddingProcessor",
        FakeEmbeddingProcessor,
    )

    event = {
        "Records": [
            {"body": json.dumps({"job_id": JOB_ID, "image_id": IMAGE_ID})}
        ]
    }
    response = handler_module.lambda_handler(event, None)

    assert response["statusCode"] == 200
    assert calls[0] == "process_ocr_job"
    assert calls[1] == "embedding_processor_init"
    assert calls[2] == (
        "process_embeddings",
        1,
        ["line-sentinel"],
        ["word-sentinel"],
    )


# ---------------------------------------------------------------------------
# 7. Metrics-only section observability (row provenance + verification)
# ---------------------------------------------------------------------------


class _MetricsRecorder:
    def __init__(self):
        self.calls = []

    def log_metrics(self, metrics, dimensions=None, properties=None):
        self.calls.append(
            {
                "metrics": metrics,
                "dimensions": dimensions,
                "properties": properties,
            }
        )


def test_section_observability_emits_all_counters(monkeypatch):
    from handler import handler as handler_module

    recorder = _MetricsRecorder()
    monkeypatch.setattr(handler_module, "emf_metrics", recorder)

    handler_module._emit_section_observability(
        IMAGE_ID,
        1,
        {
            "row_count": 12,
            "row_source": "persisted",
            "section_proposed_count": 4,
            "section_mean_confidence": 0.87,
            "verification_agreed_count": 3,
            "verification_disagreement_count": 1,
            "verification_abstained_count": 0,
        },
    )

    assert len(recorder.calls) == 1
    metrics = recorder.calls[0]["metrics"]
    assert metrics == {
        "UploadLambdaReceiptRows": 12.0,
        "UploadLambdaReceiptRowsReconstructed": 0.0,
        "UploadLambdaSectionsProposed": 4.0,
        "UploadLambdaSectionMeanConfidence": 0.87,
        "UploadLambdaSectionAgreed": 3.0,
        "UploadLambdaSectionDisagreed": 1.0,
        "UploadLambdaSectionAbstained": 0.0,
    }
    properties = recorder.calls[0]["properties"]
    assert properties["image_id"] == IMAGE_ID
    assert properties["receipt_id"] == 1
    assert properties["row_source"] == "persisted"


def test_section_observability_flags_reconstructed_rows(monkeypatch):
    from handler import handler as handler_module

    recorder = _MetricsRecorder()
    monkeypatch.setattr(handler_module, "emf_metrics", recorder)

    handler_module._emit_section_observability(
        IMAGE_ID,
        2,
        {"row_count": 5, "row_source": "reconstructed"},
    )

    metrics = recorder.calls[0]["metrics"]
    assert metrics["UploadLambdaReceiptRowsReconstructed"] == 1.0
    assert metrics["UploadLambdaReceiptRows"] == 5.0
    # Absent stats (e.g. no sections proposed key) are omitted, not zeroed.
    assert "UploadLambdaSectionsProposed" not in metrics


def test_section_observability_is_silent_without_stats(monkeypatch):
    from handler import handler as handler_module

    recorder = _MetricsRecorder()
    monkeypatch.setattr(handler_module, "emf_metrics", recorder)

    # Embedding results that carry no observability keys (e.g. failure paths)
    # must not emit an empty metric line and must never raise.
    handler_module._emit_section_observability(IMAGE_ID, 3, {"success": False})

    assert recorder.calls == []
