"""Test trace ID generation consistency across modules.

The trace ID generation logic is duplicated in:
- infra/label_evaluator_step_functions/handlers/fetch_receipt_data.py
- infra/label_evaluator_step_functions/lambdas/utils/tracing.py

This is necessary because fetch_receipt_data.py is a zip-based Lambda that
cannot import from the container-based tracing.py module. This test ensures
both implementations produce identical trace IDs for the same inputs.
"""

import sys
import uuid
from pathlib import Path

import pytest

# Add paths to import from actual source modules
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HANDLERS_PATH = (
    _PROJECT_ROOT / "infra" / "label_evaluator_step_functions" / "handlers"
)
_TRACING_PATH = (
    _PROJECT_ROOT / "infra" / "label_evaluator_step_functions" / "lambdas"
)
sys.path.insert(0, str(_HANDLERS_PATH))
sys.path.insert(0, str(_TRACING_PATH))

# Import TRACE_NAMESPACE from the actual source modules
# pylint: disable=wrong-import-position,import-error
from fetch_receipt_data import (  # noqa: E402
    TRACE_NAMESPACE as FETCH_TRACE_NAMESPACE,
)
from utils.tracing import (  # noqa: E402
    TRACE_NAMESPACE as TRACING_TRACE_NAMESPACE,
)

# Use fetch_receipt_data as the reference namespace
TRACE_NAMESPACE = FETCH_TRACE_NAMESPACE


def generate_receipt_trace_id_fetch(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
) -> str:
    """Implementation from fetch_receipt_data.py."""
    parts = [execution_arn, image_id, str(receipt_id)]
    return str(uuid.uuid5(TRACE_NAMESPACE, ":".join(parts)))


def generate_receipt_trace_id_tracing(
    execution_arn: str,
    image_id: str,
    receipt_id: int,
) -> str:
    """Implementation from tracing.py."""
    parts = [execution_arn, image_id, str(receipt_id)]
    return str(uuid.uuid5(TRACE_NAMESPACE, ":".join(parts)))


class TestTraceIdConsistency:
    """Verify trace ID generation is consistent across modules."""

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
    def test_implementations_match(
        self, execution_arn: str, image_id: str, receipt_id: int
    ):
        """Both implementations should produce identical trace IDs."""
        fetch_id = generate_receipt_trace_id_fetch(
            execution_arn, image_id, receipt_id
        )
        tracing_id = generate_receipt_trace_id_tracing(
            execution_arn, image_id, receipt_id
        )

        assert fetch_id == tracing_id, (
            f"Trace ID mismatch for ({execution_arn}, {image_id}, {receipt_id}): "
            f"fetch={fetch_id}, tracing={tracing_id}"
        )

    def test_namespace_matches(self):
        """Verify TRACE_NAMESPACE is consistent across both source modules."""
        expected = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert (
            FETCH_TRACE_NAMESPACE == expected
        ), f"fetch_receipt_data.TRACE_NAMESPACE mismatch: {FETCH_TRACE_NAMESPACE}"
        assert (
            TRACING_TRACE_NAMESPACE == expected
        ), f"tracing.TRACE_NAMESPACE mismatch: {TRACING_TRACE_NAMESPACE}"
        assert (
            FETCH_TRACE_NAMESPACE == TRACING_TRACE_NAMESPACE
        ), "TRACE_NAMESPACE differs between fetch_receipt_data.py and tracing.py"

    def test_deterministic(self):
        """Same inputs should always produce same output."""
        args = (
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
            1,
        )
        id1 = generate_receipt_trace_id_fetch(*args)
        id2 = generate_receipt_trace_id_fetch(*args)
        id3 = generate_receipt_trace_id_tracing(*args)

        assert id1 == id2 == id3

    def test_different_receipts_have_different_ids(self):
        """Different receipt_ids should produce different trace IDs."""
        base_args = (
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
        )
        id1 = generate_receipt_trace_id_fetch(*base_args, 1)
        id2 = generate_receipt_trace_id_fetch(*base_args, 2)

        assert id1 != id2

    def test_valid_uuid_format(self):
        """Generated IDs should be valid UUIDs."""
        trace_id = generate_receipt_trace_id_fetch(
            "arn:aws:states:us-west-2:123456789:execution:test:abc123",
            "img-001",
            1,
        )
        # Should not raise ValueError
        parsed = uuid.UUID(trace_id)
        assert str(parsed) == trace_id
