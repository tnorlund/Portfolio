"""Promotion tests use fake Job clients and moto S3, never live services."""

import asyncio
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

KEY = "coreml/versions/export-1/layoutlm-coreml-bundle.zip"


class FakeDynamo:
    def __init__(self, jobs=(), active=None):
        self.jobs = {job.job_id: copy.deepcopy(job) for job in jobs}
        self.active = active
        self.writes = []

    def get_job_by_name(self, name):
        return [
            copy.deepcopy(job)
            for job in self.jobs.values()
            if job.name == name
        ], None

    def get_job(self, job_id):
        if job_id not in self.jobs:
            raise EntityNotFoundError(job_id)
        return copy.deepcopy(self.jobs[job_id])

    def get_active_model_job(self):
        return next(
            (
                copy.deepcopy(job)
                for job in self.jobs.values()
                if job.tags.get("active_model") == "true"
            ),
            None,
        )

    def update_job(self, job):
        self.writes.append(("update", copy.deepcopy(job)))
        self.jobs[job.job_id] = copy.deepcopy(job)

    def add_job(self, job):
        assert job.job_id not in self.jobs
        self.writes.append(("add", copy.deepcopy(job)))
        self.jobs[job.job_id] = copy.deepcopy(job)


def job(identity=True):
    return SimpleNamespace(
        job_id="job-1",
        name="layoutlm-training-1",
        tags={"purpose": "test"},
        results={
            "best_f1": 0.8,
            **(
                {
                    "coreml_export_id": "export-1",
                    "coreml_versioned_bundle_s3_uri": f"s3://training-dev/{KEY}",
                }
                if identity
                else {}
            ),
        },
    )


@pytest.fixture
def server(monkeypatch):
    # Reuse the MCP server suite's lightweight transport stubs. They let the
    # actual implementation load without the optional mcp package.
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "tests"))
    from test_receipt_mcp_section_tools import SERVER_FILES, _load_module

    module = _load_module("promotion", SERVER_FILES["stdio"])
    monkeypatch.setitem(sys.modules, "scripts.receipt_mcp_server", module)
    monkeypatch.setattr(
        module,
        "_load_config",
        Mock(side_effect=AssertionError("Unexpected Pulumi access")),
    )
    return module


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in ("training-dev", "training-prod"):
            client.create_bucket(Bucket=bucket)
        yield client


@pytest.mark.parametrize("identity_form", ["both", "export_id", "uri", "json"])
def test_promote_writes_complete_pointer_and_flips_tags(
    server, s3, identity_form
):
    selected = job()
    if identity_form == "export_id":
        del selected.results["coreml_versioned_bundle_s3_uri"]
    elif identity_form == "uri":
        del selected.results["coreml_export_id"]
    elif identity_form == "json":
        selected.results = json.dumps(selected.results)
    old = SimpleNamespace(
        job_id="old",
        name="old",
        results={},
        tags={"active_model": "true", "keep": "yes"},
    )
    dynamo = FakeDynamo([old, selected])
    s3.put_object(Bucket="training-prod", Key=KEY, Body=b"bundle")
    result = asyncio.run(
        server.set_active_model_impl(
            dynamo,
            selected.name,
            training_bucket="training-prod",
            s3_client=s3,
        )
    )
    assert result["success"] is True
    obj = s3.get_object(Bucket="training-prod", Key="coreml/active.json")
    pointer = json.loads(obj["Body"].read())
    assert set(pointer) == {
        "schema_version",
        "export_id",
        "training_job_id",
        "training_job_name",
        "bundle_key",
        "bundle_etag",
        "bundle_size_bytes",
        "promoted_at",
        "promoted_by",
    }
    assert pointer == {
        "schema_version": 1,
        "export_id": "export-1",
        "training_job_id": "job-1",
        "training_job_name": selected.name,
        "bundle_key": KEY,
        "bundle_etag": s3.head_object(Bucket="training-prod", Key=KEY)["ETag"],
        "bundle_size_bytes": 6,
        "promoted_at": pointer["promoted_at"],
        "promoted_by": "set_active_model",
    }
    assert (
        datetime.fromisoformat(pointer["promoted_at"])
        .utcoffset()
        .total_seconds()
        == 0
    )
    assert obj["ContentType"] == "application/json"
    assert obj["CacheControl"] == "no-cache"
    assert dynamo.jobs["old"].tags == {"keep": "yes"}
    assert dynamo.jobs["job-1"].tags == {
        "purpose": "test",
        "active_model": "true",
    }


def test_promote_without_identity_does_not_flip_tag_or_load_config(server):
    selected = job(identity=False)
    dynamo = FakeDynamo([selected])
    result = asyncio.run(server.set_active_model_impl(dynamo, selected.name))
    assert result == {
        "success": False,
        "error": "job has no exported CoreML bundle; export before promoting",
    }
    assert dynamo.writes == []
    server._load_config.assert_not_called()


def test_promote_missing_bundle_does_not_flip_tags(server, s3):
    selected = job()
    dynamo = FakeDynamo([selected])
    result = asyncio.run(
        server.set_active_model_impl(dynamo, selected.name, "training-dev", s3)
    )
    assert result["success"] is False
    assert dynamo.writes == []


def test_promote_pointer_failure_restores_tags(server, s3):
    selected = job()
    old = SimpleNamespace(
        job_id="old", name="old", results={}, tags={"active_model": "true"}
    )
    dynamo = FakeDynamo([old, selected])
    s3.put_object(Bucket="training-dev", Key=KEY, Body=b"bundle")
    failing = Mock(wraps=s3)
    failing.put_object.side_effect = RuntimeError("pointer write failed")
    result = asyncio.run(
        server.set_active_model_impl(
            dynamo, selected.name, "training-dev", failing
        )
    )
    assert result == {"success": False, "error": "pointer write failed"}
    assert dynamo.jobs["old"].tags == old.tags
    assert dynamo.jobs["job-1"].tags == selected.tags


def test_promote_resolves_training_bucket_from_server_config(server, s3):
    selected = job()
    dynamo = FakeDynamo([selected])
    server._load_config.side_effect = None
    server._load_config.return_value = {
        "layoutlm_training_bucket": "training-dev"
    }
    s3.put_object(Bucket="training-dev", Key=KEY, Body=b"bundle")
    result = asyncio.run(
        server.set_active_model_impl(dynamo, selected.name, s3_client=s3)
    )
    assert result["success"] is True
    server._load_config.assert_called_once_with()


@pytest.fixture
def promotion_script(server, s3, monkeypatch):
    from scripts import promote_layoutlm_model as script

    source = FakeDynamo([job()])
    target = FakeDynamo()
    monkeypatch.setattr(
        script,
        "load_env",
        Mock(
            side_effect=lambda env: {
                "layoutlm_training_bucket": f"training-{env}",
                "dynamodb_table_name": f"table-{env}",
            }
        ),
    )
    monkeypatch.setattr(
        script,
        "DynamoClient",
        lambda table_name: source if table_name == "table-dev" else target,
    )
    monkeypatch.setattr(script.boto3, "client", lambda service: s3)
    s3.put_object(Bucket="training-dev", Key=KEY, Body=b"bundle")
    return script, source, target


def test_promote_script_prod_guard_precedes_all_reads(promotion_script):
    script, _, _ = promotion_script
    with pytest.raises(SystemExit) as error:
        script.main(["--env", "prod", "--job-name", job().name, "--dry-run"])
    assert error.value.code == 2
    script.load_env.assert_not_called()


def test_promote_script_dry_run_lists_every_write_without_mutation(
    promotion_script, s3, capsys
):
    script, source, target = promotion_script
    assert (
        script.main(
            [
                "--env",
                "prod",
                "--job-name",
                job().name,
                "--dry-run",
                "--yes-prod",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert [item["operation"] for item in plan["writes"]] == [
        "copy_bundle",
        "copy_job",
        "set_active_tag",
        "put_pointer",
    ]
    assert plan["writes"][0]["source"] == f"s3://training-dev/{KEY}"
    assert plan["writes"][0]["target"] == f"s3://training-prod/{KEY}"
    assert plan["writes"][1]["target_table"] == "table-prod"
    assert (
        plan["writes"][-1]["target"] == "s3://training-prod/coreml/active.json"
    )
    assert source.writes == target.writes == []
    assert s3.list_objects_v2(Bucket="training-prod")["KeyCount"] == 0


def test_promote_script_copies_once_then_promotes(promotion_script, s3):
    script, source, target = promotion_script
    result = asyncio.run(
        script.promote_model("prod", job().name, yes_prod=True)
    )
    assert result["success"] is True
    assert (
        s3.get_object(Bucket="training-prod", Key=KEY)["Body"].read()
        == b"bundle"
    )
    assert [action for action, _ in target.writes] == ["add", "update"]
    assert "active_model" not in target.writes[0][1].tags
    assert (
        target.jobs["job-1"].results["coreml_versioned_bundle_s3_uri"]
        == f"s3://training-prod/{KEY}"
    )
    assert source.writes == []
    again = asyncio.run(
        script.promote_model("prod", job().name, dry_run=True, yes_prod=True)
    )
    assert [item["operation"] for item in again["writes"]] == [
        "set_active_tag",
        "put_pointer",
    ]


def test_promote_script_does_not_wait_for_copied_job_name_index(
    promotion_script, s3, monkeypatch
):
    script, _, target = promotion_script
    monkeypatch.setattr(target, "get_job_by_name", lambda name: ([], None))
    result = asyncio.run(
        script.promote_model("prod", job().name, yes_prod=True)
    )
    assert result["success"] is True
    assert target.jobs["job-1"].results["coreml_versioned_bundle_etag"] == (
        s3.head_object(Bucket="training-prod", Key=KEY)["ETag"]
    )


def test_promote_pointer_write_is_conditional_on_prior_etag(server, s3):
    """A racing promotion between preflight and publish must lose: S3 is
    the arbiter, the loser's tags roll back, the pointer is untouched."""
    from botocore.exceptions import ClientError

    selected = job()
    old = SimpleNamespace(
        job_id="old", name="old", results={}, tags={"active_model": "true"}
    )
    dynamo = FakeDynamo([old, selected])
    s3.put_object(Bucket="training-dev", Key=KEY, Body=b"bundle")
    prior = s3.put_object(
        Bucket="training-dev",
        Key="coreml/active.json",
        Body=b'{"export_id": "someone-else"}',
    )
    racing = Mock(wraps=s3)
    racing.put_object.side_effect = ClientError(
        {
            "Error": {"Code": "PreconditionFailed", "Message": "raced"},
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "PutObject",
    )
    result = asyncio.run(
        server.set_active_model_impl(
            dynamo, selected.name, "training-dev", racing
        )
    )
    assert result["success"] is False
    assert "PreconditionFailed" in result["error"]
    # The write was conditional on the ETag read during preflight.
    sent = racing.put_object.call_args.kwargs
    assert sent["Key"] == "coreml/active.json"
    assert sent["IfMatch"] == prior["ETag"]
    assert "IfNoneMatch" not in sent
    # Loser rolls back; the pointer still names the winner.
    assert dynamo.jobs["old"].tags == old.tags
    assert dynamo.jobs["job-1"].tags == selected.tags
    body = s3.get_object(Bucket="training-dev", Key="coreml/active.json")
    assert json.loads(body["Body"].read()) == {"export_id": "someone-else"}


def test_promote_first_pointer_write_requires_absence(server, s3):
    selected = job()
    dynamo = FakeDynamo([selected])
    s3.put_object(Bucket="training-dev", Key=KEY, Body=b"bundle")
    spy = Mock(wraps=s3)
    result = asyncio.run(
        server.set_active_model_impl(
            dynamo, selected.name, "training-dev", spy
        )
    )
    assert result["success"] is True
    sent = spy.put_object.call_args.kwargs
    assert sent["IfNoneMatch"] == "*"
    assert "IfMatch" not in sent
