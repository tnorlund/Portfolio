"""Cross-entity contracts for job, queue, and compaction records."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from receipt_dynamo import (
    BatchSummary,
    CompactionRun,
    CoreMLExportJob,
    Instance,
    InstanceJob,
    Job,
    Queue,
    QueueJob,
    item_to_coreml_export_job,
    item_to_instance_job,
    item_to_job,
)
from receipt_dynamo.constants import BatchStatus, BatchType

NOW = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_job(**overrides):
    """Build a valid Job, overriding only the field under test."""
    values = {
        "job_id": str(uuid4()),
        "name": "training",
        "description": "contract test",
        "created_at": NOW,
        "created_by": "tester",
        "status": "pending",
        "priority": "medium",
        "job_config": {},
    }
    return Job(**(values | overrides))


def make_instance(**overrides):
    """Build a valid Instance, overriding only the field under test."""
    values = {
        "instance_id": "i-contract",
        "instance_type": "g5.xlarge",
        "gpu_count": 1,
        "status": "running",
        "launched_at": NOW,
        "ip_address": "127.0.0.1",
        "availability_zone": "us-east-1a",
        "is_spot": False,
        "health_status": "healthy",
    }
    return Instance(**(values | overrides))


def make_queue(**overrides):
    """Build a valid Queue, overriding only the field under test."""
    values = {
        "queue_name": "training",
        "description": "contract test",
        "created_at": NOW,
    }
    return Queue(**(values | overrides))


def make_queue_job(**overrides):
    """Build a valid QueueJob, overriding only the field under test."""
    values = {
        "queue_name": "training",
        "job_id": str(uuid4()),
        "enqueued_at": NOW,
    }
    return QueueJob(**(values | overrides))


def make_compaction_run(**overrides):
    """Build a valid CompactionRun, overriding only the field under test."""
    values = {
        "run_id": str(uuid4()),
        "image_id": str(uuid4()),
        "receipt_id": 1,
        "lines_delta_prefix": "lines/",
        "words_delta_prefix": "words/",
    }
    return CompactionRun(**(values | overrides))


def make_batch_summary(**overrides):
    """Build a valid BatchSummary, overriding only the field under test."""
    values = {
        "batch_id": "batch-1",
        "batch_type": BatchType.EMBEDDING,
        "openai_batch_id": "openai-1",
        "submitted_at": NOW,
        "status": BatchStatus.PENDING,
        "result_file_id": "file-1",
    }
    return BatchSummary(**(values | overrides))


def make_export_job(**overrides):
    """Build a valid CoreMLExportJob with stable defaults."""
    values = {
        "export_id": str(uuid4()),
        "job_id": str(uuid4()),
        "model_s3_uri": "s3://models/checkpoint/",
        "created_at": NOW,
    }
    return CoreMLExportJob(**(values | overrides))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "overrides", "match"),
    [
        (make_job, {"estimated_duration": True}, "positive integer"),
        (make_instance, {"gpu_count": False}, "non-negative integer"),
        (make_queue, {"max_concurrent_jobs": True}, "positive integer"),
        (make_queue, {"job_count": False}, "non-negative integer"),
        (make_queue_job, {"position": True}, "non-negative integer"),
        (make_compaction_run, {"receipt_id": True}, "must be an integer"),
        (
            make_compaction_run,
            {"lines_merged_vectors": False},
            "must be an integer",
        ),
        (
            make_compaction_run,
            {"words_merged_vectors": True},
            "must be an integer",
        ),
        (
            make_batch_summary,
            {"receipt_refs": [(str(uuid4()), True)]},
            "must be an integer",
        ),
        (
            make_export_job,
            {"model_size_bytes": True},
            "non-negative integer",
        ),
        (
            make_export_job,
            {"export_duration_seconds": False},
            "non-negative finite",
        ),
    ],
)
def test_integer_fields_reject_bool(factory, overrides, match):
    """A Python bool must never be serialized as a DynamoDB number."""
    with pytest.raises(ValueError, match=match):
        factory(**overrides)


@pytest.mark.unit
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_coreml_export_duration_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="non-negative finite"):
        make_export_job(export_duration_seconds=value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"model_size_bytes": -1}, "non-negative integer"),
        ({"export_duration_seconds": -0.1}, "non-negative finite"),
        ({"output_s3_prefix": 1}, "output_s3_prefix must be a string"),
        ({"mlpackage_s3_uri": 1}, "mlpackage_s3_uri must be a string"),
        ({"bundle_s3_uri": 1}, "bundle_s3_uri must be a string"),
        ({"error_message": 1}, "error_message must be a string"),
    ],
)
def test_coreml_export_optional_field_validation(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_export_job(**overrides)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"export_id": "bad"}, "valid UUIDv4"),
        ({"job_id": "bad"}, "valid UUIDv4"),
        ({"model_s3_uri": ""}, "model_s3_uri must be a non-empty string"),
        ({"model_s3_uri": 1}, "model_s3_uri must be a non-empty string"),
        ({"created_at": "2024-01-01"}, "created_at must be a datetime"),
        ({"status": "BAD"}, "CoreMLExportStatus must be one of"),
        ({"quantize": "bad"}, "quantize must be one of"),
        ({"updated_at": "2024-01-01"}, "updated_at must be a datetime"),
        ({"completed_at": "2024-01-01"}, "completed_at must be a datetime"),
    ],
)
def test_coreml_export_required_field_validation(overrides, match):
    with pytest.raises(ValueError, match=match):
        make_export_job(**overrides)


@pytest.mark.unit
def test_coreml_export_full_and_minimal_round_trips():
    full = make_export_job(
        status="SUCCEEDED",
        quantize="float16",
        output_s3_prefix="s3://exports/job/",
        updated_at=NOW,
        completed_at=NOW,
        mlpackage_s3_uri="s3://exports/job/model.mlpackage",
        bundle_s3_uri="s3://exports/job/bundle/",
        model_size_bytes=0,
        export_duration_seconds=0.0,
        error_message="",
    )
    minimal = make_export_job()

    assert item_to_coreml_export_job(full.to_item()) == full
    assert item_to_coreml_export_job(minimal.to_item()) == minimal
    assert full.key == {
        "PK": {"S": f"COREML_EXPORT#{full.export_id}"},
        "SK": {"S": "EXPORT"},
    }
    assert full.gsi1_key()["GSI1PK"] == {"S": f"JOB#{full.job_id}"}
    assert full.gsi2_key()["GSI2PK"] == {"S": "COREML_EXPORT_STATUS#SUCCEEDED"}
    assert minimal.to_item()["model_size_bytes"] == {"NULL": True}


@pytest.mark.unit
def test_coreml_export_value_semantics():
    export = make_export_job()
    duplicate = CoreMLExportJob(**dict(export))
    different = make_export_job()

    assert export == duplicate
    assert hash(export) == hash(duplicate)
    assert export != different
    assert export != "not an export"
    assert dict(export)["status"] == "PENDING"
    assert export.export_id in repr(export)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda item: item.pop("status"), "missing keys"),
        (lambda item: item.__setitem__("created_at", {"S": "bad"}), "Error"),
        (lambda item: item.__setitem__("status", {"S": "BAD"}), "Error"),
    ],
)
def test_coreml_export_rejects_invalid_items(mutator, match):
    item = make_export_job().to_item()
    mutator(item)
    with pytest.raises(ValueError, match=match):
        item_to_coreml_export_job(item)


@pytest.mark.unit
def test_nested_job_maps_round_trip_without_type_loss():
    nested = {
        "model": {"layers": [12, 24], "enabled": True},
        "threshold": 0.75,
        "optional": None,
        "labels": {"merchant", "total"},
    }
    job = make_job(job_config=nested, results={"metrics": nested})

    assert item_to_job(job.to_item()) == job


@pytest.mark.unit
def test_nested_instance_utilization_round_trip_without_type_loss():
    utilization = {
        "cpu": {"percent": 12.5, "cores": [0, 1]},
        "gpu": {"active": True, "memory_mb": None},
    }
    assignment = InstanceJob(
        instance_id="i-contract",
        job_id=str(uuid4()),
        assigned_at=NOW,
        status="running",
        resource_utilization=utilization,
    )

    assert item_to_instance_job(assignment.to_item()) == assignment


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"job_config": {"bad": float("nan")}},
        {"results": {"bad": float("inf")}},
    ],
)
def test_job_nested_maps_reject_non_finite_numbers(payload):
    with pytest.raises(ValueError, match="numbers must be finite"):
        make_job(**payload).to_item()


@pytest.mark.unit
def test_instance_utilization_rejects_non_finite_numbers():
    assignment = InstanceJob(
        instance_id="i-contract",
        job_id=str(uuid4()),
        assigned_at=NOW,
        status="running",
        resource_utilization={"gpu": {"temperature": float("nan")}},
    )

    with pytest.raises(ValueError, match="numbers must be finite"):
        assignment.to_item()


@pytest.mark.unit
@pytest.mark.parametrize(
    "receipt_refs",
    [
        [("image",)],
        [["image", 1]],
        [("", 1)],
        [(1, 1)],
        [("image", -1)],
        [("image", "1")],
    ],
)
def test_batch_summary_validates_each_receipt_reference(receipt_refs):
    with pytest.raises(ValueError, match="receipt_refs"):
        make_batch_summary(receipt_refs=receipt_refs)


@pytest.mark.unit
def test_default_collections_are_not_shared_between_entities():
    first_batch = make_batch_summary()
    second_batch = make_batch_summary()
    first_batch.receipt_refs.append(("image", 1))

    first_job = make_job()
    second_job = make_job()
    first_job.tags["environment"] = "test"

    first_assignment = InstanceJob("i-one", str(uuid4()), NOW, "assigned")
    second_assignment = InstanceJob("i-two", str(uuid4()), NOW, "assigned")
    first_assignment.resource_utilization["cpu"] = 1

    assert not second_batch.receipt_refs
    assert second_job.tags == {}
    assert second_assignment.resource_utilization == {}


@pytest.mark.unit
def test_job_tags_require_string_keys_and_values():
    for tags in ({1: "value"}, {"key": 1}):
        with pytest.raises(
            ValueError, match="keys and values must be strings"
        ):
            make_job(tags=tags)


@pytest.mark.unit
@pytest.mark.parametrize("field_name", ["lines_error", "words_error"])
def test_compaction_errors_require_strings(field_name):
    with pytest.raises(ValueError, match=f"{field_name} must be a string"):
        make_compaction_run(**{field_name: 1})
