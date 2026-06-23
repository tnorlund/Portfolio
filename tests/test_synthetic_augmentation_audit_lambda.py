"""Tests for the synthetic augmentation audit Lambda handler helpers."""

import importlib.util
import io
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "lambdas"
    / "synthetic_augmentation_audit.py"
)


class FakePaginator:
    def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3 style
        assert Bucket == "pattern-bucket"
        assert Prefix == "line_item_patterns/execution-1/"
        return [
            {
                "Contents": [
                    {"Key": "line_item_patterns/execution-1/a.json"},
                    {"Key": "line_item_patterns/execution-1/readme.txt"},
                    {"Key": "line_item_patterns/execution-1/b.json"},
                ]
            }
        ]


class FakeS3:
    def __init__(self):
        self.puts = []
        self.objects = {
            "line_item_patterns/execution-1/a.json": {
                "synthetic_receipt_plan": {
                    "recipes": [
                        {
                            "actual_label": "B-MERCHANT_NAME",
                            "predicted_label": "O",
                        }
                    ]
                }
            },
            "line_item_patterns/execution-1/b.json": {
                "synthetic_receipt_plan": {
                    "recipes": [
                        {
                            "actual_label": "O",
                            "predicted_label": "ADDRESS",
                        }
                    ]
                }
            },
        }

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return FakePaginator()

    def get_object(self, Bucket, Key):  # noqa: N803 - boto3 style
        assert Bucket == "pattern-bucket"
        return {"Body": io.BytesIO(json.dumps(self.objects[Key]).encode("utf-8"))}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"ETag": '"test"'}


class FakeSageMaker:
    def list_tags(self, ResourceArn):  # noqa: N803 - boto3 style
        assert ResourceArn == "arn:aws:sagemaker:training-job/layoutlm-aug"
        return {
            "Tags": [
                {"Key": "synthetic-augmentation", "Value": "true"},
                {"Key": "baseline-job-ref", "Value": "layoutlm-baseline"},
                {
                    "Key": "line-item-patterns-s3-prefix",
                    "Value": "line_item_patterns/execution-1/",
                },
                {"Key": "batch-bucket", "Value": "pattern-bucket"},
            ]
        }


def _load_module(monkeypatch):
    import boto3

    def fake_client(service_name):
        if service_name == "s3":
            return FakeS3()
        if service_name == "sagemaker":
            return FakeSageMaker()
        raise AssertionError(service_name)

    monkeypatch.setattr(boto3, "client", fake_client)
    spec = importlib.util.spec_from_file_location(
        "synthetic_augmentation_audit_lambda_for_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_pattern_artifacts_accepts_s3_prefix(monkeypatch):
    module = _load_module(monkeypatch)

    artifacts = module._load_pattern_artifacts(
        {
            "batch_bucket": "pattern-bucket",
            "line_item_patterns_s3_prefix": "line_item_patterns/execution-1/",
        }
    )

    assert len(artifacts) == 2
    assert artifacts[0]["synthetic_receipt_plan"]["recipes"][0] == {
        "actual_label": "B-MERCHANT_NAME",
        "predicted_label": "O",
    }


def test_completion_event_uses_sagemaker_tags(monkeypatch):
    module = _load_module(monkeypatch)

    payload = module._merge_sagemaker_completion_event(
        {
            "detail": {
                "TrainingJobName": "layoutlm-aug",
                "TrainingJobArn": ("arn:aws:sagemaker:training-job/layoutlm-aug"),
            }
        }
    )

    assert payload == {
        "augmented_job_ref": "layoutlm-aug",
        "baseline_job_ref": "layoutlm-baseline",
        "line_item_patterns_s3_prefix": "line_item_patterns/execution-1/",
        "batch_bucket": "pattern-bucket",
    }


def test_write_audit_result_persists_decision_json(monkeypatch):
    module = _load_module(monkeypatch)

    output = module._write_audit_result(
        {
            "batch_bucket": "pattern-bucket",
            "audit_output_prefix": "synthetic_audits",
        },
        augmented_job_ref="layoutlm-augmented/run 1",
        result={"recommendation": "promote"},
    )

    assert output == {
        "audit_result_s3_bucket": "pattern-bucket",
        "audit_result_s3_key": ("synthetic_audits/layoutlm-augmented-run-1.json"),
        "audit_result_s3_uri": (
            "s3://pattern-bucket/synthetic_audits/" "layoutlm-augmented-run-1.json"
        ),
    }
    assert module.s3.puts[0]["Bucket"] == "pattern-bucket"
    assert module.s3.puts[0]["Key"] == (
        "synthetic_audits/layoutlm-augmented-run-1.json"
    )
    assert json.loads(module.s3.puts[0]["Body"].decode("utf-8")) == {
        "recommendation": "promote"
    }
    assert module.s3.puts[0]["ContentType"] == "application/json"
