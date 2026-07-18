"""Tests for applying fix-place results to existing records."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

MODULE_PATH = (
    Path(__file__).parent / "fix_place_lambda" / "lambdas" / "fix_place.py"
)
SPEC = importlib.util.spec_from_file_location("fix_place_handler", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
FIX_PLACE_HANDLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX_PLACE_HANDLER)
_update_receipt_place = FIX_PLACE_HANDLER._update_receipt_place


def test_explicit_empty_phone_clears_stale_existing_phone(monkeypatch):
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "test-table")
    current_place = SimpleNamespace(
        place_id="old-place",
        merchant_name="Old Merchant",
        formatted_address="1 Old St",
        phone_number="(702) 555-0199",
        confidence=0.1,
        reasoning="old",
        validated_by="HUMAN",
        validation_status="UNSURE",
        timestamp=None,
    )
    dynamo_client = MagicMock()
    agent_result = {
        "place_id": "new-place",
        "merchant_name": "New Merchant",
        "address": "2 New St",
        "phone_number": "",
        "confidence": 0.95,
        "reasoning": "Tiered address match has no phone.",
    }

    with patch(
        "receipt_dynamo.DynamoClient",
        return_value=dynamo_client,
    ):
        updated = _update_receipt_place(
            image_id="image-1",
            receipt_id=1,
            current_place=current_place,
            agent_result=agent_result,
            reason="Incorrect merchant",
        )

    assert updated.phone_number == ""
    dynamo_client.update_receipt_place.assert_called_once_with(current_place)
