import uuid
from datetime import datetime, timezone

import pytest

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
)
from receipt_dynamo.entities.synthetic_training_set import (
    SyntheticTrainingSet,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def synth_dynamo(dynamodb_table):
    return DynamoClient(table_name=dynamodb_table)


def _sample(status="draft", set_id=None, content_hash="abc123"):
    return SyntheticTrainingSet(
        set_id=set_id or str(uuid.uuid4()),
        name="vons-taxable-2026-06",
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by="tester",
        status=status,
        bundle_s3_uri="s3://bucket/synth/vons-bundle.json",
        content_hash=content_hash,
        merchants=["Vons"],
        accepted_count=5,
        candidates_seen=48,
        candidates_rejected=43,
        operation_counts={"add_line_item": 5},
        mix_balance={"risk_level": "low", "top_merchant_share": "1.0"},
        source_receipt_keys=["img1#1", "img2#1"],
        gate_versions={"quality_gate": "1", "min_structure_similarity": "0.60"},
        generation_config={"max_per_merchant": "100"},
    )


def test_round_trip_to_from_item():
    s = _sample()
    assert SyntheticTrainingSet.from_item(s.to_item()) == s


def test_invalid_status_rejected():
    with pytest.raises(ValueError):
        _sample(status="bogus")


def test_keys_and_gsis():
    s = _sample(status="approved", content_hash="deadbeef")
    assert s.key["PK"]["S"] == f"SYNTHETIC_SET#{s.set_id}"
    assert s.key["SK"]["S"] == "SYNTHETIC_SET"
    assert s.gsi1_key()["GSI1PK"]["S"] == "SYNTH_SET_STATUS#approved"
    assert s.gsi2_key()["GSI2PK"]["S"] == "SYNTH_SET_HASH#deadbeef"


def test_add_and_get(synth_dynamo):
    s = _sample()
    synth_dynamo.add_synthetic_training_set(s)
    assert synth_dynamo.get_synthetic_training_set(s.set_id) == s


def test_get_missing_raises(synth_dynamo):
    with pytest.raises(EntityNotFoundError):
        synth_dynamo.get_synthetic_training_set(str(uuid.uuid4()))


def test_list_by_status(synth_dynamo):
    a = _sample(status="approved")
    p = _sample(status="pending_review")
    synth_dynamo.add_synthetic_training_set(a)
    synth_dynamo.add_synthetic_training_set(p)
    approved, _ = synth_dynamo.list_synthetic_training_sets_by_status("approved")
    ids = {x.set_id for x in approved}
    assert a.set_id in ids and p.set_id not in ids


def test_get_by_hash(synth_dynamo):
    s = _sample(content_hash="uniquehash123")
    synth_dynamo.add_synthetic_training_set(s)
    found = synth_dynamo.get_synthetic_training_set_by_hash("uniquehash123")
    assert found is not None and found.set_id == s.set_id
    assert synth_dynamo.get_synthetic_training_set_by_hash("nope") is None


def test_approve_moves_gsi(synth_dynamo):
    s = _sample(status="pending_review")
    synth_dynamo.add_synthetic_training_set(s)
    approved = synth_dynamo.approve_synthetic_training_set(
        s.set_id, "reviewer", datetime.now(timezone.utc).isoformat()
    )
    assert approved.status == "approved" and approved.approved_by == "reviewer"
    appr, _ = synth_dynamo.list_synthetic_training_sets_by_status("approved")
    assert s.set_id in {x.set_id for x in appr}


def test_link_to_job_idempotent(synth_dynamo):
    s = _sample()
    synth_dynamo.add_synthetic_training_set(s)
    synth_dynamo.link_synthetic_training_set_to_job(s.set_id, "job-123")
    synth_dynamo.link_synthetic_training_set_to_job(s.set_id, "job-123")
    assert synth_dynamo.get_synthetic_training_set(s.set_id).used_by_jobs == [
        "job-123"
    ]
