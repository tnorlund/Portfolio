"""The CORE_LABELS vocabulary guard for receipt word labels.

A word label's name is part of its DynamoDB sort key
(``...#WORD#00001#LABEL#<label>``), so every distinct string a writer emits
mints a new pseudo-label-type. Production already carries 394 rows across 72
malformed label strings written by a since-fixed free-text parser (#758).

These tests pin the two halves of the contract:

* **Write** -- ``add_receipt_word_label(s)`` is the only path that can mint a
  new label sort key (``attribute_not_exists``), and it refuses any label
  outside ``CORE_LABELS``.
* **Read** -- nothing on a read or read-modify-write path may raise on the
  existing malformed corpus. The fixture below is a byte-for-byte copy of a
  real production row from ``ReceiptsTable-d7ff76a``.
"""

from dataclasses import asdict

import pytest

from receipt_dynamo.constants import (
    CORE_LABEL_NAMES,
    CORE_LABELS,
    NON_CORE_LABEL_ALIASES,
    canonical_label_name,
    invalid_label_message,
    is_core_label,
    normalize_core_label,
    normalize_label_alias,
)
from receipt_dynamo.data._receipt_word_label import _ReceiptWordLabel
from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_word_label import (
    ReceiptWordLabel,
    item_to_receipt_word_label,
)

IMAGE_ID = "344f4a1b-1476-442e-bb01-7eed30934285"

# A real malformed row read (read-only) out of production table
# ReceiptsTable-d7ff76a. Written 2026-01-18 by ``label-evaluator-llm``, the
# free-text parser fixed by #758. Reading it must never raise.
STORED_MALFORMED_ITEM = {
    "PK": {"S": f"IMAGE#{IMAGE_ID}"},
    "SK": {
        "S": (
            "RECEIPT#00001#LINE#00010#WORD#00001"
            "#LABEL#LINE_TOTAL (SHOULD BE 69.60)"
        )
    },
    "TYPE": {"S": "RECEIPT_WORD_LABEL"},
    "validation_status": {"S": "INVALID"},
    "label_consolidated_from": {"NULL": True},
    "label_proposed_by": {"S": "label-evaluator-llm"},
    "timestamp_added": {"S": "2026-01-18T17:12:51.520489+00:00"},
    "reasoning": {
        "S": (
            "[label-evaluator phase1] Decision: INVALID (high). "
            '"3.25" is a price amount; it belongs to UNIT_PRICE, '
            "not LINE_TOTAL."
        )
    },
}

MALFORMED_LABEL = "LINE_TOTAL (SHOULD BE 69.60)"

# Other real shapes from the malformed corpus: a whole sentence and a bare
# amount. Both were minted purely because nothing checked the vocabulary.
JUNK_LABELS = (
    "SUBTOTAL SHOULD BE $214.46 (OR THE $4014.97 ENTRY SHOULD BE "
    "CORRECTED/REMOVED)",
    "7829.53",
    MALFORMED_LABEL,
)


class _Validator(_ReceiptWordLabel):
    """The add-path validator in isolation -- it never touches the network."""

    table_name = "ReceiptsTable-test"


def _label(label: str, **overrides) -> ReceiptWordLabel:
    kwargs = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "line_id": 10,
        "word_id": 1,
        "label": label,
        "reasoning": "because",
        "timestamp_added": "2026-08-05T00:00:00+00:00",
    }
    kwargs.update(overrides)
    return ReceiptWordLabel(**kwargs)


# ---------------------------------------------------------------------------
# Vocabulary helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_core_label_names_matches_core_labels():
    assert set(CORE_LABEL_NAMES) == set(CORE_LABELS)
    assert list(CORE_LABEL_NAMES) == sorted(CORE_LABELS)
    assert len(CORE_LABEL_NAMES) == 22


@pytest.mark.unit
def test_aliases_all_point_at_core_labels():
    for alias, target in NON_CORE_LABEL_ALIASES.items():
        assert alias not in CORE_LABELS
        assert target in CORE_LABELS


@pytest.mark.unit
@pytest.mark.parametrize("label", sorted(CORE_LABELS))
def test_core_label_normalizes_to_itself(label):
    assert is_core_label(label)
    assert normalize_core_label(label.lower()) == label
    assert normalize_label_alias(f"  {label}  ") == label


@pytest.mark.unit
@pytest.mark.parametrize(
    "alias,expected", sorted(NON_CORE_LABEL_ALIASES.items())
)
def test_alias_normalizes_to_core_label(alias, expected):
    assert not is_core_label(alias)
    assert normalize_core_label(alias) == expected
    assert normalize_core_label(alias.lower()) == expected


@pytest.mark.unit
@pytest.mark.parametrize("junk", JUNK_LABELS)
def test_junk_label_is_refused_with_a_clear_message(junk):
    assert normalize_label_alias(junk) is None
    with pytest.raises(ValueError) as excinfo:
        normalize_core_label(junk)
    message = str(excinfo.value)
    assert "label must be one of" in message
    assert "GRAND_TOTAL" in message
    assert canonical_label_name(junk) in message


@pytest.mark.unit
def test_invalid_label_message_suggests_a_known_alias():
    message = invalid_label_message("address")
    assert "label must be one of" in message
    assert "Did you mean 'ADDRESS_LINE'?" in message


# ---------------------------------------------------------------------------
# WRITE path: add_receipt_word_label(s) is the only sort-key minting path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_add_validator_accepts_a_core_label():
    _Validator()._validate_receipt_word_labels_for_add([_label("GRAND_TOTAL")])


@pytest.mark.unit
@pytest.mark.parametrize("junk", JUNK_LABELS)
def test_add_validator_refuses_junk_before_any_write(junk):
    with pytest.raises(EntityValidationError) as excinfo:
        _Validator()._validate_receipt_word_labels_for_add([_label(junk)])
    message = str(excinfo.value)
    assert "Cannot add non-core receipt word label" in message
    assert "must be one of" in message
    assert "GRAND_TOTAL" in message


@pytest.mark.unit
def test_add_validator_refuses_an_alias_and_names_the_target():
    with pytest.raises(EntityValidationError) as excinfo:
        _Validator()._validate_receipt_word_labels_for_add([_label("ADDRESS")])
    assert "did you mean 'ADDRESS_LINE'?" in str(excinfo.value)


@pytest.mark.unit
def test_normalized_alias_then_passes_the_add_validator():
    normalized = normalize_core_label("ADDRESS")
    _Validator()._validate_receipt_word_labels_for_add([_label(normalized)])
    assert _label(normalized).key["SK"]["S"].endswith("#LABEL#ADDRESS_LINE")


@pytest.mark.unit
def test_legacy_restore_escape_hatch_is_explicit():
    """The only way past the guard is an explicit, named argument."""
    _Validator()._validate_receipt_word_labels_for_add(
        [_label(MALFORMED_LABEL)], allow_non_core_labels=True
    )


# ---------------------------------------------------------------------------
# READ path: the existing malformed corpus must stay fully readable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stored_malformed_row_still_deserialises():
    """from_item on a real production malformed row must not raise."""
    entity = ReceiptWordLabel.from_item(STORED_MALFORMED_ITEM)
    assert entity.label == MALFORMED_LABEL
    assert entity.image_id == IMAGE_ID
    assert entity.validation_status == "INVALID"

    # ...and via the module-level converter the data layer actually calls.
    assert item_to_receipt_word_label(STORED_MALFORMED_ITEM) == entity


@pytest.mark.unit
def test_stored_malformed_row_round_trips_through_to_item():
    """to_item() re-runs __post_init__; a legacy row must survive it."""
    entity = ReceiptWordLabel.from_item(STORED_MALFORMED_ITEM)
    item = entity.to_item()
    assert item["SK"] == STORED_MALFORMED_ITEM["SK"]
    assert ReceiptWordLabel.from_item(item) == entity


@pytest.mark.unit
def test_stored_malformed_row_round_trips_through_asdict():
    """Step Functions and the copy scripts rebuild labels from plain dicts."""
    entity = ReceiptWordLabel.from_item(STORED_MALFORMED_ITEM)
    assert ReceiptWordLabel(**asdict(entity)) == entity
    assert ReceiptWordLabel(**dict(entity)) == entity


@pytest.mark.unit
def test_read_modify_write_of_a_malformed_row_is_not_blocked():
    """Marking a legacy malformed row INVALID must keep working.

    ``update_receipt_word_label`` is conditional on ``attribute_exists(PK)``,
    so it cannot mint a new sort key and therefore must NOT run the
    vocabulary guard -- otherwise the tooling needed to triage the 394
    malformed rows would be the first casualty of the guard.
    """
    entity = ReceiptWordLabel.from_item(STORED_MALFORMED_ITEM)
    updated = ReceiptWordLabel(
        image_id=entity.image_id,
        receipt_id=entity.receipt_id,
        line_id=entity.line_id,
        word_id=entity.word_id,
        label=entity.label,
        reasoning="triage: malformed label name",
        timestamp_added=entity.timestamp_added,
        validation_status="INVALID",
        label_proposed_by="mcp-claude-review",
        label_consolidated_from=entity.label_consolidated_from,
    )
    assert updated.label == MALFORMED_LABEL
    assert updated.to_item()["SK"] == STORED_MALFORMED_ITEM["SK"]
