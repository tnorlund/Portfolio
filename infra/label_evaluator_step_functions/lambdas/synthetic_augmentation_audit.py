"""Audit synthetic LayoutLM augmentation runs from Dynamo metrics."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

import boto3

s3 = boto3.client("s3")
sagemaker = boto3.client("sagemaker")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return slug[:160] or "unknown-job"


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if isinstance(body, str) and body.strip():
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return event
        if isinstance(parsed, dict):
            return {**event, **parsed}
    return event


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def _load_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read().decode("utf-8")
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError(
            f"S3 object s3://{bucket}/{key} did not contain JSON object"
        )
    return data


def _load_json_prefix_from_s3(
    bucket: str, prefix: str
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item.get("Key")
            if isinstance(key, str) and key.endswith(".json"):
                artifacts.append(_load_json_from_s3(bucket, key))
    return artifacts


def _load_pattern_artifacts(event: dict[str, Any]) -> list[dict[str, Any]]:
    artifact = event.get("pattern_artifact") or event.get("line_item_patterns")
    if isinstance(artifact, dict):
        return [artifact]

    s3_uri = (
        event.get("pattern_artifact_s3_uri")
        or event.get("line_item_patterns_s3_uri")
        or event.get("synthetic_training_examples")
    )
    if isinstance(s3_uri, str) and s3_uri.startswith("s3://"):
        bucket, key = _parse_s3_uri(s3_uri)
        if key.endswith("/"):
            return _load_json_prefix_from_s3(bucket, key)
        return [_load_json_from_s3(bucket, key)]

    bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    key = event.get("line_item_patterns_s3_key") or event.get(
        "pattern_artifact_s3_key"
    )
    if bucket and key:
        return [_load_json_from_s3(str(bucket), str(key))]

    prefix = event.get("line_item_patterns_s3_prefix") or event.get(
        "pattern_artifact_s3_prefix"
    )
    if bucket and prefix:
        return _load_json_prefix_from_s3(str(bucket), str(prefix))

    return []


def _event_tags(training_job_arn: str | None) -> dict[str, str]:
    if not training_job_arn:
        return {}
    response = sagemaker.list_tags(ResourceArn=training_job_arn)
    return {
        str(tag.get("Key")): str(tag.get("Value"))
        for tag in response.get("Tags", [])
        if tag.get("Key") is not None and tag.get("Value") is not None
    }


def _merge_sagemaker_completion_event(
    event: dict[str, Any],
) -> dict[str, Any] | None:
    detail = event.get("detail") or {}
    job_name = detail.get("TrainingJobName")
    if not job_name:
        return None

    tags = _event_tags(detail.get("TrainingJobArn"))
    if tags.get("synthetic-augmentation") != "true":
        return {
            "status": "skipped",
            "reason": "not_synthetic_augmentation",
            "augmented_job_ref": job_name,
        }

    payload = {
        "augmented_job_ref": job_name,
        "baseline_job_ref": tags.get("baseline-job-ref"),
        "pattern_artifact_s3_uri": tags.get("pattern-artifact-s3-uri"),
        "line_item_patterns_s3_key": tags.get("line-item-patterns-s3-key"),
        "line_item_patterns_s3_prefix": tags.get(
            "line-item-patterns-s3-prefix"
        ),
        "batch_bucket": tags.get("batch-bucket"),
    }
    return {key: value for key, value in payload.items() if value}


def _job_ref(event: dict[str, Any], *names: str) -> str:
    for name in names:
        value = event.get(name)
        if isinstance(value, str) and value:
            return value
    raise ValueError(
        f"Missing required job reference: one of {', '.join(names)}"
    )


def _audit_output_bucket(event: dict[str, Any]) -> str | None:
    value = (
        event.get("audit_output_bucket")
        or event.get("batch_bucket")
        or os.environ.get("BATCH_BUCKET")
    )
    return str(value) if value else None


def _audit_output_key(event: dict[str, Any], augmented_job_ref: str) -> str:
    key = event.get("audit_output_s3_key")
    if key:
        return str(key)
    prefix = str(
        event.get("audit_output_prefix") or "synthetic_augmentation_audits"
    ).strip("/")
    return f"{prefix}/{_slug(augmented_job_ref)}.json"


def _write_audit_result(
    event: dict[str, Any],
    *,
    augmented_job_ref: str,
    result: dict[str, Any],
) -> dict[str, str]:
    bucket = _audit_output_bucket(event)
    if not bucket:
        return {}
    key = _audit_output_key(event, augmented_job_ref)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(result, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )
    return {
        "audit_result_s3_bucket": bucket,
        "audit_result_s3_key": key,
        "audit_result_s3_uri": f"s3://{bucket}/{key}",
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Compare baseline and synthetic-augmented LayoutLM training runs.

    Required input:
    - baseline_job_ref, baseline_job_id, or baseline_job_name
    - augmented_job_ref, augmented_job_id, or augmented_job_name
    - pattern_artifact, synthetic_plan, target_pairs, or S3 pointer to the
      pattern-builder artifact containing ``synthetic_receipt_plan``
    """
    from receipt_agent.agents.label_evaluator.augmentation_audit import (
        build_synthetic_augmentation_audit_from_dynamo,
    )
    from receipt_dynamo import DynamoClient

    payload = _event_payload(event)
    completion_payload = _merge_sagemaker_completion_event(payload)
    if completion_payload and completion_payload.get("status") == "skipped":
        return completion_payload
    if completion_payload:
        payload = {**payload, **completion_payload}

    baseline_job_ref = _job_ref(
        payload,
        "baseline_job_ref",
        "baseline_job_id",
        "baseline_job_name",
    )
    augmented_job_ref = _job_ref(
        payload,
        "augmented_job_ref",
        "augmented_job_id",
        "augmented_job_name",
    )
    pattern_artifacts = _load_pattern_artifacts(payload)
    synthetic_plan = payload.get("synthetic_plan")
    target_pairs = payload.get("target_pairs")
    if not target_pairs and len(pattern_artifacts) > 1:
        from receipt_agent.agents.label_evaluator.augmentation_audit import (
            target_pairs_from_pattern_artifact,
        )

        pairs: list[tuple[str, str]] = []
        for artifact in pattern_artifacts:
            for pair in target_pairs_from_pattern_artifact(artifact):
                if pair not in pairs:
                    pairs.append(pair)
        target_pairs = pairs

    table_name = (
        payload.get("dynamodb_table_name")
        or os.environ.get("DYNAMODB_TABLE_NAME")
        or os.environ.get("RECEIPT_AGENT_DYNAMO_TABLE_NAME")
        or "ReceiptsTable"
    )
    dynamo_client = DynamoClient(table_name=table_name)

    audit = build_synthetic_augmentation_audit_from_dynamo(
        dynamo_client=dynamo_client,
        baseline_job_ref=baseline_job_ref,
        augmented_job_ref=augmented_job_ref,
        pattern_artifact=(
            pattern_artifacts[0] if len(pattern_artifacts) == 1 else None
        ),
        synthetic_plan=(
            synthetic_plan if isinstance(synthetic_plan, dict) else None
        ),
        target_pairs=target_pairs,
        min_confusion_reduction=float(
            payload.get("min_confusion_reduction", 0.05)
        ),
        max_val_f1_drop=float(payload.get("max_val_f1_drop", 0.005)),
        min_synthetic_acceptance_rate=float(
            payload.get("min_synthetic_acceptance_rate", 0.5)
        ),
        min_accepted_candidate_quality=float(
            payload.get("min_accepted_candidate_quality", 0.75)
        ),
    )

    result = {
        "baseline_job_ref": baseline_job_ref,
        "augmented_job_ref": augmented_job_ref,
        "recommendation": audit.recommendation,
        "pattern_artifact_count": len(pattern_artifacts),
        "audit": audit.to_dict(),
    }
    result.update(
        _write_audit_result(
            payload,
            augmented_job_ref=augmented_job_ref,
            result=result,
        )
    )
    return result
