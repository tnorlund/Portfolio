"""Test the active receipt trace ID contract."""

import sys
import uuid
from pathlib import Path

import pytest

# Add paths to import from actual source modules
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TRACING_PATH = (
    _PROJECT_ROOT / "infra" / "label_evaluator_step_functions" / "lambdas"
)
sys.path.insert(0, str(_TRACING_PATH))

# Import the contract from the active unified evaluator source.
# pylint: disable=wrong-import-position,import-error
from utils.tracing import (  # noqa: E402
    TRACE_NAMESPACE,
    generate_receipt_trace_id,
)


class TestReceiptTraceId:
    """Verify the active receipt trace ID contract."""

    @pytest.mark.parametrize(
        "execution_arn,image_id,receipt_id",
        [
            (
                "arn:aws:states:us-west-2:123456789:execution:test:abc123",
                "img-001",
                1,
            ),
            (
                "arn:aws:states:us-west-2:123456789:execution:test:abc123",
                "img-001",
                2,
            ),
            (
                "arn:aws:states:us-west-2:123456789:execution:prod:xyz789",
                "a1b2c3d4-e5f6-47a8-89b0-c1d2e3f4a5b6",
                0,
            ),
            # Edge cases
            ("", "", 0),
            ("arn:with:colons", "id:with:colons", 999),
        ],
    )
    def test_matches_uuid5_contract(
        self, execution_arn: str, image_id: str, receipt_id: int
    ):
        """The public helper should retain its deterministic UUID5 contract."""
        actual = generate_receipt_trace_id(execution_arn, image_id, receipt_id)
        expected = str(
            uuid.uuid5(
                TRACE_NAMESPACE,
                ":".join([execution_arn, image_id, str(receipt_id)]),
            )
        )

        assert actual == expected

    def test_namespace_matches(self):
        """Verify the pinned namespace remains stable."""
        expected = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert TRACE_NAMESPACE == expected

    def test_deterministic(self):
        """Same inputs should always produce same output."""
        args = (
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
            1,
        )
        id1 = generate_receipt_trace_id(*args)
        id2 = generate_receipt_trace_id(*args)

        assert id1 == id2

    def test_different_receipts_have_different_ids(self):
        """Different receipt_ids should produce different trace IDs."""
        base_args = (
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
        )
        id1 = generate_receipt_trace_id(*base_args, 1)
        id2 = generate_receipt_trace_id(*base_args, 2)

        assert id1 != id2

    def test_valid_uuid_format(self):
        """Generated IDs should be valid UUIDs."""
        trace_id = generate_receipt_trace_id(
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
            1,
        )
        # Should not raise ValueError
        parsed = uuid.UUID(trace_id)
        assert str(parsed) == trace_id
