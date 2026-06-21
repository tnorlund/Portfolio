"""Tests for the shared async/sync LLM validation runner.

The ``receipt_upload.label_validation`` package __init__ imports the Chroma
validator (heavy, and broken under Python 3.14), so we load ``llm_runner`` from
its file directly and stub the two lazily-imported heavy deps
(``receipt_agent.constants`` and the LLM validator). This exercises the JSON
hand-off and the grok-apply decision logic without standing up Chroma/OpenRouter.
"""

import importlib.util
import json
import os
import sys
import types
from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "receipt_upload",
    "label_validation",
    "llm_runner.py",
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("llm_runner_ut", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load_runner()

IMAGE_ID = "00000000-0000-4000-8000-000000000abc"
_CORE = {"PRODUCT_NAME", "LINE_TOTAL", "MERCHANT_NAME", "ADDRESS_LINE"}


def _label(line_id, word_id, label, status=ValidationStatus.PENDING.value):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="r",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=status,
    )


# --------------------------------------------------------------------------- #
# Serialization + payload shape (no heavy deps).
# --------------------------------------------------------------------------- #
def test_label_json_round_trip():
    lab = _label(5, 2, "LINE_TOTAL")
    d = m._label_to_jsonable(lab)
    # Must be JSON-safe (timestamp serialized to a string).
    s = json.dumps(d)
    back = m._label_from_jsonable(json.loads(s))
    assert back.label == "LINE_TOTAL"
    assert (back.line_id, back.word_id) == (5, 2)
    assert back.validation_status == ValidationStatus.PENDING.value


def test_build_async_payload_is_json_safe_and_complete():
    # Real ReceiptWord entities (build_async_payload serializes affected words).
    words = [_word(8, 1, "MILK"), _word(8, 2, "$5.99")]
    llm_needed = [(words[0], _label(8, 1, "PRODUCT_NAME"))]

    class _FakeLV:
        def _query_similar_for_label(self, **kw):
            return [{"text": "BREAD", "label": "PRODUCT_NAME"}]

    payload = m.build_async_payload(
        llm_needed=llm_needed,
        words=words,
        image_id=IMAGE_ID,
        receipt_id=1,
        table_name="tbl",
        lightweight_validator=_FakeLV(),
        word_embedding_cache={(8, 1): [0.1] * 4},
    )
    json.dumps(payload)  # raises if anything is not JSON-safe
    assert payload["image_id"] == IMAGE_ID
    assert payload["table_name"] == "tbl"
    assert len(payload["needed_labels"]) == 1
    assert payload["pending_labels_data"][0]["word_text"] == "MILK"
    assert payload["similar_evidence"]["8_1"]  # evidence carried in payload
    # Affected word + aligned embedding carried for the corrective resync.
    assert len(payload["affected_words"]) == 1
    assert payload["affected_embeddings"] == [[0.1] * 4]


# --------------------------------------------------------------------------- #
# Apply path (stub the lazily-imported heavy deps).
# --------------------------------------------------------------------------- #
class _FakeDynamo:
    def __init__(self, labels_for_receipt=None):
        self.updated = []
        self.added = []
        self.deleted = []
        self.compaction_runs = []
        self._labels_for_receipt = labels_for_receipt or []

    def update_receipt_word_label(self, label):
        self.updated.append(label)

    def add_receipt_word_label(self, label):
        self.added.append(label)

    def delete_receipt_word_label(self, label):
        self.deleted.append(label)

    def list_receipt_word_labels_for_receipt(self, image_id, receipt_id):
        return list(self._labels_for_receipt), None


def _install_stubs(results, raises=None):
    """Stub receipt_agent.constants + the LLM validator module in sys.modules."""
    const = types.ModuleType("receipt_agent.constants")
    const.CORE_LABELS = _CORE
    agent_pkg = sys.modules.get("receipt_agent") or types.ModuleType("receipt_agent")

    validator_mod = types.ModuleType("receipt_upload.label_validation.llm_validator")

    class _FakeValidator:
        def __init__(self, *a, **k):
            pass

        def validate_receipt_labels(self, **kwargs):
            if raises is not None:
                raise raises
            return results

    validator_mod.LLMBatchValidator = _FakeValidator
    lv_pkg = types.ModuleType("receipt_upload.label_validation")

    saved = {
        k: sys.modules.get(k)
        for k in (
            "receipt_agent",
            "receipt_agent.constants",
            "receipt_upload.label_validation",
            "receipt_upload.label_validation.llm_validator",
        )
    }
    sys.modules["receipt_agent"] = agent_pkg
    sys.modules["receipt_agent.constants"] = const
    sys.modules["receipt_upload.label_validation"] = lv_pkg
    sys.modules["receipt_upload.label_validation.llm_validator"] = validator_mod
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _result(line, word, decision, label, reasoning="x"):
    return SimpleNamespace(
        word_id=f"{line}_{word}",
        decision=decision,
        label=label,
        reasoning=reasoning,
    )


def test_apply_async_payload_valid_invalid_and_correction():
    needed = [
        _label(5, 2, "PRODUCT_NAME"),  # -> VALID
        _label(6, 1, "ADDRESS_LINE"),  # -> INVALID + corrected to MERCHANT_NAME
        _label(7, 1, "PRODUCT_NAME"),  # -> INVALID (same label)
    ]
    payload = {
        "version": 1,
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "table_name": "tbl",
        "merchant_name": None,
        "llm_words_context": [],
        "pending_labels_data": [],
        "similar_evidence": {},
        "needed_labels": [m._label_to_jsonable(lab) for lab in needed],
    }
    results = [
        _result(5, 2, "VALID", "PRODUCT_NAME"),
        _result(6, 1, "INVALID", "MERCHANT_NAME"),
        _result(7, 1, "INVALID", "PRODUCT_NAME"),
    ]
    dynamo = _FakeDynamo()
    saved = _install_stubs(results)
    try:
        n = m.apply_async_payload(payload, dynamo)
    finally:
        _restore(saved)

    assert n == 3
    # The correction added a new MERCHANT_NAME (VALID, consolidated from ADDRESS).
    added = [lab for lab in dynamo.added if lab.label == "MERCHANT_NAME"]
    assert len(added) == 1
    assert added[0].validation_status == ValidationStatus.VALID.value
    assert added[0].label_consolidated_from == "ADDRESS_LINE"
    # The VALID and the two INVALIDs were status updates.
    statuses = {
        (lab.line_id, lab.word_id): lab.validation_status for lab in dynamo.updated
    }
    assert statuses[(5, 2)] == ValidationStatus.VALID.value
    assert statuses[(6, 1)] == ValidationStatus.INVALID.value
    assert statuses[(7, 1)] == ValidationStatus.INVALID.value


def _payload(needed):
    return {
        "version": 1,
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "table_name": "tbl",
        "merchant_name": None,
        "llm_words_context": [],
        "pending_labels_data": [],
        "similar_evidence": {},
        "needed_labels": [m._label_to_jsonable(lab) for lab in needed],
    }


def test_apply_async_payload_raises_on_llm_failure_and_keeps_labels():
    """A grok failure must propagate (so the consumer reports a batch-item
    failure → SQS redrive/DLQ) and must NOT clean up the pending labels."""
    needed = [_label(5, 2, "PRODUCT_NAME")]
    dynamo = _FakeDynamo()
    saved = _install_stubs([], raises=RuntimeError("openrouter 503"))
    try:
        raised = False
        try:
            m.apply_async_payload(_payload(needed), dynamo)
        except RuntimeError:
            raised = True
    finally:
        _restore(saved)
    assert raised, "apply_async_payload must re-raise on LLM failure"
    # Labels left intact for retry — nothing deleted.
    assert dynamo.deleted == []


def test_apply_async_payload_idempotent_on_duplicate_correction():
    """On redelivery the corrected label already exists; a duplicate add must be
    swallowed (EntityAlreadyExistsError) and still count as resolved."""
    from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError

    needed = [_label(6, 1, "ADDRESS_LINE")]
    results = [_result(6, 1, "INVALID", "MERCHANT_NAME")]

    class _DupDynamo(_FakeDynamo):
        def add_receipt_word_label(self, label):
            raise EntityAlreadyExistsError("already exists")

    dynamo = _DupDynamo()
    saved = _install_stubs(results)
    try:
        n = m.apply_async_payload(_payload(needed), dynamo)
    finally:
        _restore(saved)
    # Did not crash; the correction is counted despite the duplicate add.
    assert n == 1


# --------------------------------------------------------------------------- #
# Corrective Chroma resync (consumer side).
# --------------------------------------------------------------------------- #
def _word(line_id, word_id, text):
    from receipt_dynamo.entities import ReceiptWord

    return ReceiptWord(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": 0.1, "y": 0.7, "width": 0.1, "height": 0.02},
        top_left={"x": 0.1, "y": 0.72},
        top_right={"x": 0.2, "y": 0.72},
        bottom_left={"x": 0.1, "y": 0.70},
        bottom_right={"x": 0.2, "y": 0.70},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.99,
    )


def _install_chroma_stubs(calls):
    """Stub the receipt_chroma functions resync imports."""
    from dataclasses import asdict

    mod = types.ModuleType("receipt_chroma")

    def build_words_payload(
        *, receipt_words, word_embeddings_list, word_labels, merchant_name
    ):
        calls["build"] = {
            "n_words": len(receipt_words),
            "n_emb": len(word_embeddings_list),
            "labels": [(l.line_id, l.word_id, l.label) for l in word_labels],
        }
        return {"ids": ["x"]}, {}

    def upload_words_delta(*, word_payload, run_id, chromadb_bucket, s3_client):
        calls["upload"] = {"run_id": run_id, "bucket": chromadb_bucket}
        return f"words/delta/{run_id}"

    def create_compaction_run(
        *, run_id, image_id, receipt_id, lines_prefix, words_prefix, dynamo_client
    ):
        calls["compaction"] = {
            "run_id": run_id,
            "lines_prefix": lines_prefix,
            "words_prefix": words_prefix,
        }
        return object()

    mod.build_words_payload = build_words_payload
    mod.upload_words_delta = upload_words_delta
    mod.create_compaction_run = create_compaction_run
    saved = sys.modules.get("receipt_chroma")
    sys.modules["receipt_chroma"] = mod
    return saved


def test_resync_returns_none_without_resync_data():
    # v1-style payload (no lines_prefix / affected words) -> no-op.
    assert (
        m.resync_corrected_labels_to_chroma(
            {"image_id": IMAGE_ID, "receipt_id": 1}, _FakeDynamo()
        )
        is None
    )


def test_resync_builds_delta_and_compaction_run():
    from dataclasses import asdict

    w = _word(8, 1, "MILK")
    # DynamoDB holds the FINAL (post-grok) label for the affected word + an
    # unrelated label that must NOT be included.
    final = _label(8, 1, "PRODUCT_NAME", ValidationStatus.VALID.value)
    other = _label(99, 9, "MERCHANT_NAME", ValidationStatus.VALID.value)
    dynamo = _FakeDynamo(labels_for_receipt=[final, other])
    payload = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "merchant_name": "X",
        "lines_prefix": "lines/delta/run-abc",
        "chromadb_bucket": "bucket-1",
        "affected_words": [asdict(w)],
        "affected_embeddings": [[0.1] * 4],
    }
    calls = {}
    saved = _install_chroma_stubs(calls)
    try:
        run_id = m.resync_corrected_labels_to_chroma(payload, dynamo)
    finally:
        if saved is None:
            sys.modules.pop("receipt_chroma", None)
        else:
            sys.modules["receipt_chroma"] = saved

    assert run_id and calls["compaction"]["run_id"] == run_id
    assert calls["compaction"]["lines_prefix"] == "lines/delta/run-abc"
    assert calls["compaction"]["words_prefix"] == f"words/delta/{run_id}"
    # Only the affected word's label is in the delta (the 99/9 one is excluded).
    assert calls["build"]["labels"] == [(8, 1, "PRODUCT_NAME")]
    assert calls["build"]["n_words"] == 1 and calls["build"]["n_emb"] == 1
