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
    words = [
        SimpleNamespace(text="MILK", line_id=8, word_id=1),
        SimpleNamespace(text="$5.99", line_id=8, word_id=2),
    ]
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


# --------------------------------------------------------------------------- #
# Apply path (stub the lazily-imported heavy deps).
# --------------------------------------------------------------------------- #
class _FakeDynamo:
    def __init__(self):
        self.updated = []
        self.added = []
        self.deleted = []

    def update_receipt_word_label(self, label):
        self.updated.append(label)

    def add_receipt_word_label(self, label):
        self.added.append(label)

    def delete_receipt_word_label(self, label):
        self.deleted.append(label)


def _install_stubs(results):
    """Stub receipt_agent.constants + the LLM validator module in sys.modules."""
    const = types.ModuleType("receipt_agent.constants")
    const.CORE_LABELS = _CORE
    agent_pkg = sys.modules.get("receipt_agent") or types.ModuleType("receipt_agent")

    validator_mod = types.ModuleType("receipt_upload.label_validation.llm_validator")

    class _FakeValidator:
        def __init__(self, *a, **k):
            pass

        def validate_receipt_labels(self, **kwargs):
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
        "needed_labels": [m._label_to_jsonable(l) for l in needed],
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
    added = [l for l in dynamo.added if l.label == "MERCHANT_NAME"]
    assert len(added) == 1
    assert added[0].validation_status == ValidationStatus.VALID.value
    assert added[0].label_consolidated_from == "ADDRESS_LINE"
    # The VALID and the two INVALIDs were status updates.
    statuses = {(l.line_id, l.word_id): l.validation_status for l in dynamo.updated}
    assert statuses[(5, 2)] == ValidationStatus.VALID.value
    assert statuses[(6, 1)] == ValidationStatus.INVALID.value
    assert statuses[(7, 1)] == ValidationStatus.INVALID.value
