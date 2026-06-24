#!/usr/bin/env python3
"""Launch and verify the synthetic LayoutLM replay proof path.

This script intentionally separates preflight checks from execution. Use
``status`` after deployment to confirm the label-evaluator Step Function,
audit Lambda, EventBridge rule, and audit S3 prefix are ready. Use ``start``
to launch a replay from top confusion-pair pattern artifacts, then optionally
wait for the Step Function and audit JSON.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "receipt_agent"))
sys.path.insert(0, str(PROJECT_ROOT / "receipt_layoutlm"))

_MISSING_MODULE = object()


SUCCESS_EXECUTION_STATUSES = {"SUCCEEDED"}
TERMINAL_EXECUTION_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "ABORTED",
}
SYNTHESIS_OPERATION_FAMILIES = (
    "hard_negative",
    "add_line_item",
    "remove_line_item",
    "replace_field",
)
DEFAULT_EXPERIMENT_MAX_AWS_SPEND_USD = 200.0
LLM_MODEL_FRESHNESS_MAX_AGE_DAYS = 30
LLM_MODEL_FRESHNESS_CHECK_DATE_ENV = "RECEIPT_AGENT_MODEL_FRESHNESS_CHECK_DATE"


def load_outputs(env: str) -> dict[str, Any]:
    """Load Pulumi stack outputs for the requested environment."""
    from receipt_dynamo.data._pulumi import load_env

    outputs = load_env(env)
    if not outputs:
        raise RuntimeError(f"No Pulumi outputs found for stack {env!r}")
    return outputs


def json_print(payload: dict[str, Any]) -> None:
    """Print a stable JSON document for shell pipelines and audits."""
    print(json.dumps(payload, indent=2, sort_keys=True))


def _client(service: str, region: str):
    return boto3.client(service, region_name=region)


def _function_exists(lambda_client, function_name: str | None) -> bool:
    if not function_name:
        return False
    try:
        lambda_client.get_function(FunctionName=function_name)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return False
        raise


def _list_audit_keys(s3_client, bucket: str, limit: int) -> list[str]:
    response = s3_client.list_objects_v2(
        Bucket=bucket,
        Prefix="synthetic_augmentation_audits/",
        MaxKeys=limit,
    )
    return [item["Key"] for item in response.get("Contents", [])]


def describe_deployment(
    outputs: dict[str, Any],
    *,
    env: str,
    region: str,
    audit_key_limit: int = 10,
) -> dict[str, Any]:
    """Inspect deployed replay/audit resources."""
    state_machine_arn = outputs.get("label_evaluator_sf_arn")
    batch_bucket = outputs.get("label_evaluator_batch_bucket_name")
    audit_lambda_arn = outputs.get("synthetic_augmentation_audit_lambda_arn")
    audit_lambda_name = (
        audit_lambda_arn or f"label-evaluator-{env}-synthetic-augmentation-audit"
    )

    sfn = _client("stepfunctions", region)
    lambda_client = _client("lambda", region)
    events = _client("events", region)
    s3 = _client("s3", region)

    definition = ""
    state_machine_status = None
    if state_machine_arn:
        state_machine = sfn.describe_state_machine(stateMachineArn=state_machine_arn)
        definition = state_machine.get("definition", "")
        state_machine_status = state_machine.get("status")

    rules = events.list_rules(
        NamePrefix=f"label-evaluator-{env}-synthetic-augmentation"
    ).get("Rules", [])

    audit_keys: list[str] = []
    if batch_bucket:
        audit_keys = _list_audit_keys(s3, str(batch_bucket), audit_key_limit)

    return {
        "state_machine_arn": state_machine_arn,
        "state_machine_status": state_machine_status,
        "state_machine_has_synthetic_replay": ("CheckRunSyntheticReplay" in definition),
        "state_machine_preserves_replay_result": (
            "synthetic_replay_result" in definition
        ),
        "layoutlm_start_training_lambda": outputs.get("layoutlm_start_training_lambda"),
        "audit_lambda_arn": audit_lambda_arn,
        "audit_lambda_exists": _function_exists(lambda_client, audit_lambda_name),
        "audit_event_rules": [
            {"name": rule.get("Name"), "state": rule.get("State")} for rule in rules
        ],
        "batch_bucket": batch_bucket,
        "audit_keys": audit_keys,
        "ready": bool(
            state_machine_arn
            and "CheckRunSyntheticReplay" in definition
            and "synthetic_replay_result" in definition
            and _function_exists(lambda_client, audit_lambda_name)
            and rules
        ),
    }


def require_ready(status: dict[str, Any]) -> None:
    """Fail fast when deployed replay resources are not ready."""
    missing: list[str] = []
    if not status.get("state_machine_has_synthetic_replay"):
        missing.append("Step Function synthetic replay states")
    if not status.get("state_machine_preserves_replay_result"):
        missing.append("Step Function terminal synthetic_replay_result output")
    if not status.get("audit_lambda_exists"):
        missing.append("synthetic augmentation audit Lambda")
    if not status.get("audit_event_rules"):
        missing.append("synthetic augmentation EventBridge rule")
    if missing:
        raise RuntimeError("Synthetic replay is not deployed: " + ", ".join(missing))


def parse_json_arg(value: str | None) -> dict[str, Any]:
    """Parse an optional JSON object argument."""
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed


def build_execution_input(args: argparse.Namespace) -> dict[str, Any]:
    """Build the label-evaluator execution payload."""
    hyperparameters = parse_json_arg(args.hyperparameters)
    if args.epochs is not None:
        hyperparameters["epochs"] = str(args.epochs)
    if args.early_stopping_patience is not None:
        hyperparameters["early_stopping_patience"] = str(args.early_stopping_patience)

    payload: dict[str, Any] = {
        "run_synthetic_replay": True,
        "synthetic_replay_cost_ack": args.confirm_cost_ack,
        "baseline_job_ref": args.baseline_job_ref,
        "run_analytics": args.run_analytics,
        "synthetic_replay_hyperparameters": hyperparameters,
        "synthetic_replay_instance_type": args.instance_type,
        "synthetic_replay_instance_count": args.instance_count,
        "synthetic_replay_use_spot": args.use_spot,
        "synthetic_replay_max_runtime_hours": args.max_runtime_hours,
    }
    if args.limit is not None:
        payload["limit"] = args.limit
    if args.since_date:
        payload["since_date"] = args.since_date
    if args.langchain_project:
        payload["langchain_project"] = args.langchain_project
    return payload


def validate_start_args(args: argparse.Namespace) -> None:
    """Fail fast before submitting an uncapped synthetic replay."""
    if not args.confirm_cost_ack:
        raise RuntimeError(
            "--confirm-cost-ack is required before starting real infrastructure"
        )
    if args.limit is None or args.limit < 1 or args.limit > 3:
        raise RuntimeError("--limit must be between 1 and 3 for this smoke test")
    if args.instance_count != 1:
        raise RuntimeError("--instance-count must be 1 for synthetic replay")
    if not args.use_spot:
        raise RuntimeError("synthetic replay requires managed spot: omit --no-spot")
    if args.max_runtime_hours > 1:
        raise RuntimeError("--max-runtime-hours must be 1 or less")
    if args.epochs is not None and args.epochs > 1:
        raise RuntimeError("--epochs must be 1 for synthetic replay")


def _current_month_cost_window(today: date | None = None) -> tuple[str, str]:
    """Return Cost Explorer's inclusive start/exclusive end date window."""
    today = today or datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    end = today + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def current_month_aws_spend_usd(*, region: str) -> dict[str, Any]:
    """Read current-month unblended AWS spend from Cost Explorer."""
    start, end = _current_month_cost_window()
    ce = _client("ce", region)
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    total = (
        response.get("ResultsByTime", [{}])[0].get("Total", {}).get("UnblendedCost", {})
    )
    amount = _safe_float(total.get("Amount"))
    return {
        "start": start,
        "end": end,
        "amount_usd": round(amount or 0.0, 2),
        "raw_amount_usd": total.get("Amount"),
        "unit": total.get("Unit") or "USD",
        "metric": "UnblendedCost",
    }


def require_experiment_budget_remaining(
    *,
    region: str,
    max_aws_spend_usd: float | None,
) -> dict[str, Any]:
    """Fail closed when account month-to-date spend is at/above the cap."""
    if max_aws_spend_usd is None or max_aws_spend_usd <= 0:
        return {"enforced": False}
    spend = current_month_aws_spend_usd(region=region)
    amount = _safe_float(spend.get("raw_amount_usd"))
    if amount is None:
        raise RuntimeError("Unable to read current AWS spend from Cost Explorer")
    if amount >= max_aws_spend_usd:
        raise RuntimeError(
            "AWS experiment spend cap reached: "
            f"${amount:.2f} month-to-date >= ${max_aws_spend_usd:.2f}. "
            "Not starting synthetic replay."
        )
    return {
        "enforced": True,
        "max_aws_spend_usd": round(max_aws_spend_usd, 2),
        "remaining_usd": round(max_aws_spend_usd - amount, 2),
        **spend,
    }


def _default_experiment_max_aws_spend_usd() -> float:
    raw = os.getenv("SYNTHETIC_REPLAY_MAX_AWS_SPEND_USD")
    if raw is None:
        return DEFAULT_EXPERIMENT_MAX_AWS_SPEND_USD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_EXPERIMENT_MAX_AWS_SPEND_USD


def start_execution(
    outputs: dict[str, Any],
    *,
    region: str,
    payload: dict[str, Any],
    name_prefix: str,
) -> dict[str, Any]:
    """Start the deployed label-evaluator Step Function."""
    state_machine_arn = outputs["label_evaluator_sf_arn"]
    started_at = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    execution_name = f"{name_prefix}-{started_at}"
    sfn = _client("stepfunctions", region)
    response = sfn.start_execution(
        stateMachineArn=state_machine_arn,
        name=execution_name,
        input=json.dumps(payload),
    )
    return {
        "execution_arn": response["executionArn"],
        "start_date": response["startDate"].isoformat(),
        "input": payload,
    }


def wait_for_execution(
    execution_arn: str,
    *,
    region: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Poll a Step Function execution until it reaches a terminal state."""
    sfn = _client("stepfunctions", region)
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = sfn.describe_execution(executionArn=execution_arn)
        status = last.get("status")
        if status in TERMINAL_EXECUTION_STATUSES:
            output = {}
            if last.get("output"):
                output = json.loads(last["output"])
            return {
                "execution_arn": execution_arn,
                "status": status,
                "output": output,
                "stop_date": (
                    last.get("stopDate").isoformat() if last.get("stopDate") else None
                ),
            }
        time.sleep(poll_seconds)

    return {
        "execution_arn": execution_arn,
        "status": last.get("status", "UNKNOWN"),
        "timed_out": True,
    }


def _parse_lambda_result_body(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    body = result.get("body")
    if isinstance(body, str):
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    return body if isinstance(body, dict) else result


def extract_training_job_name(execution_output: dict[str, Any]) -> str | None:
    """Extract the SageMaker job name from Step Function terminal output."""
    replay_result = execution_output.get("synthetic_replay_result")
    parsed = _parse_lambda_result_body(replay_result)
    job_name = parsed.get("job_name")
    return str(job_name) if job_name else None


def _audit_key_for_job(job_name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in job_name)
    slug = "-".join(part for part in slug.split("-") if part)
    return f"synthetic_augmentation_audits/{slug}.json"


def fetch_audit_result(
    *,
    bucket: str,
    job_name: str,
    region: str,
) -> dict[str, Any] | None:
    """Read the audit JSON for a synthetic replay job if it exists."""
    s3 = _client("s3", region)
    key = _audit_key_for_job(job_name)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404"}:
            return None
        raise
    return json.loads(response["Body"].read().decode("utf-8"))


def wait_for_audit_result(
    *,
    bucket: str,
    job_name: str,
    region: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    """Poll S3 for audit JSON written by the audit Lambda."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        audit = fetch_audit_result(
            bucket=bucket,
            job_name=job_name,
            region=region,
        )
        if audit is not None:
            return audit
        time.sleep(poll_seconds)
    return None


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    numeric = _safe_float(value)
    return int(numeric) if numeric is not None else None


def _artifact_merchant(artifact: dict[str, Any]) -> str:
    profile = artifact.get("merchant_receipt_parameterization") or {}
    return str(
        artifact.get("merchant_name")
        or profile.get("merchant_name")
        or "Unknown merchant"
    )


def _candidate_is_grounded(candidate: dict[str, Any]) -> bool:
    metadata = _candidate_metadata(candidate)
    added_item = metadata.get("added_item") or {}
    observed = metadata.get("observed_item_evidence") or {}
    return bool(
        added_item.get("seen_in_other_receipt")
        or observed.get("product_seen_outside_base")
    )


def _candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _source_key_values(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_source_key_values(item))
        return values
    return []


def _unique_source_keys(*values: Any) -> list[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(_source_key_values(value))
    return sorted(keys)


def _candidate_source_lineage(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = _candidate_metadata(candidate)
    accuracy = metadata.get("synthesis_accuracy_evidence")
    accuracy = accuracy if isinstance(accuracy, dict) else {}
    structure = metadata.get("structure_similarity")
    structure = structure if isinstance(structure, dict) else {}
    accuracy_structure = accuracy.get("structure_similarity")
    if isinstance(accuracy_structure, dict):
        if not structure:
            structure = accuracy_structure
        elif not structure.get("nearest_real_receipt_key"):
            structure = {**accuracy_structure, **structure}
    catalog = accuracy.get("catalog_grounding")
    catalog = catalog if isinstance(catalog, dict) else {}
    observed = metadata.get("observed_item_evidence")
    observed = observed if isinstance(observed, dict) else {}
    added = metadata.get("added_item")
    added = added if isinstance(added, dict) else {}
    layout = metadata.get("layout_integrity") or accuracy.get("layout_integrity")
    layout = layout if isinstance(layout, dict) else {}
    arithmetic = metadata.get("arithmetic_reconciliation")
    arithmetic = arithmetic if isinstance(arithmetic, dict) else {}
    selection = metadata.get("selection_evidence")

    base_key = str(metadata.get("base_receipt_key") or "").strip()
    nearest_key = str(structure.get("nearest_real_receipt_key") or "").strip()
    product_keys = _unique_source_keys(
        added.get("source_receipt_keys"),
        observed.get("product_seen_outside_base"),
        catalog.get("product_seen_outside_base"),
    )
    category_keys = _unique_source_keys(
        observed.get("category_seen_in_receipts"),
        catalog.get("category_seen_in_receipts"),
    )
    source_keys = _unique_source_keys(
        base_key,
        nearest_key,
        product_keys,
        category_keys,
    )
    flags = {
        "has_base_receipt": bool(base_key),
        "has_cross_receipt_item": (
            any(key != base_key for key in product_keys)
            if base_key
            else bool(product_keys)
        ),
        "has_category_evidence": bool(category_keys),
        "has_nearest_real_structure": bool(nearest_key),
        "has_layout_integrity": bool(layout),
        "has_arithmetic_reconciliation": bool(arithmetic),
        "has_selection_evidence": isinstance(selection, dict),
    }
    if not source_keys and not any(flags.values()):
        return {}
    result = {
        "schema_version": "synthetic-candidate-lineage-v1",
        "base_receipt_key": base_key or None,
        "nearest_real_receipt_key": nearest_key or None,
        "source_receipt_key_count": len(source_keys),
        "source_receipt_keys": source_keys[:12],
        "product_source_receipt_keys": product_keys[:8],
        "category_source_receipt_keys": category_keys[:8],
        "evidence_flags": flags,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _candidate_merchant(candidate: dict[str, Any]) -> str:
    metadata = _candidate_metadata(candidate)
    profile = metadata.get("profile")
    merchant = candidate.get("merchant_name")
    if not merchant and isinstance(profile, dict):
        merchant = profile.get("merchant_name")
    return str(merchant or "Unknown merchant")


def _candidate_operation(candidate: dict[str, Any]) -> str:
    metadata = _candidate_metadata(candidate)
    return str(metadata.get("operation") or candidate.get("operation") or "unknown")


def _candidate_category(candidate: dict[str, Any]) -> str:
    metadata = _candidate_metadata(candidate)
    added_item = metadata.get("added_item")
    removed_item = metadata.get("removed_item")
    for item in (added_item, removed_item):
        if isinstance(item, dict):
            category = str(item.get("category") or "").strip()
            if category:
                return category
    observed = metadata.get("observed_item_evidence")
    if isinstance(observed, dict):
        category = str(observed.get("category") or "").strip()
        if category:
            return category
    return "unknown"


def _candidate_field_replacement_label(candidate: dict[str, Any]) -> str | None:
    metadata = _candidate_metadata(candidate)
    replacement = metadata.get("field_replacement")
    if not isinstance(replacement, dict):
        return None
    label = str(replacement.get("label") or "").strip()
    return label or None


def _candidate_structure_score(candidate: dict[str, Any]) -> float | None:
    structure = _candidate_metadata(candidate).get("structure_similarity")
    if not isinstance(structure, dict):
        return None
    return _safe_float(structure.get("score"))


def _candidate_structure_components(candidate: dict[str, Any]) -> dict[str, float]:
    structure = _candidate_metadata(candidate).get("structure_similarity")
    if not isinstance(structure, dict):
        return {}
    components = structure.get("components")
    if not isinstance(components, dict):
        return {}
    result: dict[str, float] = {}
    for name, raw_value in components.items():
        value = _safe_float(raw_value)
        if value is not None:
            result[str(name)] = value
    return result


def _candidate_real_baseline_comparison(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    structure = _candidate_metadata(candidate).get("structure_similarity")
    if not isinstance(structure, dict):
        return None
    baseline = structure.get("real_baseline_comparison")
    return baseline if isinstance(baseline, dict) else None


def _candidate_quality(candidate: dict[str, Any]) -> dict[str, Any] | None:
    quality = _candidate_metadata(candidate).get("candidate_quality")
    if not isinstance(quality, dict):
        return None
    components = {
        str(name): value
        for name, raw_value in (quality.get("components") or {}).items()
        if (value := _safe_float(raw_value)) is not None
    }
    result = {
        "score": _safe_float(quality.get("score")),
        "high_fidelity": quality.get("high_fidelity") is True,
        "components": components,
    }
    if isinstance(quality.get("structure_gate"), dict):
        result["structure_gate"] = quality["structure_gate"]
    return {key: value for key, value in result.items() if value not in (None, {})}


def _candidate_quality_score(candidate: dict[str, Any]) -> float | None:
    quality = _candidate_quality(candidate)
    if not quality:
        return None
    return _safe_float(quality.get("score"))


def _candidate_quality_components(candidate: dict[str, Any]) -> dict[str, float]:
    quality = _candidate_quality(candidate)
    components = quality.get("components") if quality else None
    return components if isinstance(components, dict) else {}


def _candidate_structure_component_pass_rate(
    candidate: dict[str, Any],
    structure_component_thresholds: dict[str, float],
) -> float | None:
    components = _candidate_structure_components(candidate)
    checked = [
        (name, threshold)
        for name, threshold in structure_component_thresholds.items()
        if name in components
    ]
    if not checked:
        return None
    passed = sum(1 for name, threshold in checked if components[name] >= threshold)
    return round(passed / len(checked), 3)


def _candidate_layout_integrity_score(candidate: dict[str, Any]) -> float | None:
    metadata = _candidate_metadata(candidate)
    accuracy = metadata.get("synthesis_accuracy_evidence")
    accuracy = accuracy if isinstance(accuracy, dict) else {}
    layout = metadata.get("layout_integrity") or accuracy.get("layout_integrity")
    if not isinstance(layout, dict):
        return None
    score = _safe_float(layout.get("score"))
    if layout.get("passed") is True:
        return score
    if layout.get("passed") is False:
        return 0.0
    return score


def _candidate_real_baseline_alignment_score(
    candidate: dict[str, Any],
) -> float | None:
    baseline = _candidate_real_baseline_comparison(candidate)
    if not isinstance(baseline, dict):
        return None
    pair_count = _safe_int(baseline.get("baseline_pair_count"))
    if pair_count is None or pair_count < 3:
        return None
    if baseline.get("within_real_score_range") is False:
        return 0.0
    candidate_score = _safe_float(baseline.get("candidate_score"))
    baseline_min = _safe_float(baseline.get("baseline_min"))
    baseline_max = _safe_float(baseline.get("baseline_max"))
    if candidate_score is None or baseline_min is None or baseline_max is None:
        return None
    return 1.0 if baseline_min <= candidate_score <= baseline_max else 0.0


def _derived_candidate_quality(
    candidate: dict[str, Any],
    *,
    min_structure_similarity: float,
    structure_component_thresholds: dict[str, float],
    quality_failure: str | None,
) -> dict[str, Any] | None:
    """Derive no-spend candidate quality from deterministic gate evidence."""
    if _candidate_quality(candidate):
        return None
    if quality_failure:
        return None

    structure_score = _candidate_structure_score(candidate)
    if structure_score is None:
        return None

    components: dict[str, float] = {"structure_similarity": structure_score}
    pass_rate = _candidate_structure_component_pass_rate(
        candidate,
        structure_component_thresholds,
    )
    if pass_rate is not None:
        components["structure_component_pass_rate"] = pass_rate

    operation_evidence = 1.0 if _candidate_has_operation_evidence(candidate) else 0.0
    components["operation_evidence"] = operation_evidence
    operation = _candidate_operation(candidate)
    if operation == "add_line_item":
        components["cross_receipt_grounding"] = (
            1.0 if _candidate_is_grounded(candidate) else 0.0
        )
    if operation in {"add_line_item", "remove_line_item"}:
        components["arithmetic_reconciliation"] = (
            1.0 if _candidate_has_arithmetic(candidate) else 0.0
        )
    if (layout_score := _candidate_layout_integrity_score(candidate)) is not None:
        components["layout_integrity"] = layout_score
    if (
        baseline_score := _candidate_real_baseline_alignment_score(candidate)
    ) is not None:
        components["real_baseline_alignment"] = baseline_score

    score = round(sum(components.values()) / len(components), 3)
    independent_evidence = any(
        components.get(name) == 1.0
        for name in ("layout_integrity", "real_baseline_alignment")
    )
    high_fidelity = (
        score >= 0.85
        and structure_score >= max(0.85, min_structure_similarity)
        and pass_rate is not None
        and pass_rate >= 1.0
        and operation_evidence >= 1.0
        and independent_evidence
    )
    return {
        "schema_version": "synthetic-candidate-quality-v1",
        "source": "deterministic_layoutlm_gate_evidence",
        "score": score,
        "high_fidelity": high_fidelity,
        "components": components,
        "structure_gate": {
            "passed": True,
            "min_structure_similarity": min_structure_similarity,
            "structure_component_thresholds": structure_component_thresholds,
            "requires_independent_layout_or_real_baseline": True,
        },
    }


def _with_derived_candidate_quality(
    rows: list[dict[str, Any]],
    *,
    min_structure_similarity: float,
    structure_component_thresholds: dict[str, float],
    quality_failure_fn: Any,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("candidate_quality"):
            enriched.append(row)
            continue
        quality = _derived_candidate_quality(
            row,
            min_structure_similarity=min_structure_similarity,
            structure_component_thresholds=structure_component_thresholds,
            quality_failure=quality_failure_fn(
                row,
                min_structure_similarity=min_structure_similarity,
            ),
        )
        if not quality:
            enriched.append(row)
            continue
        next_row = dict(row)
        next_metadata = dict(metadata)
        next_metadata["candidate_quality"] = quality
        next_row["metadata"] = next_metadata
        enriched.append(next_row)
    return enriched


def _candidate_selection_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = _candidate_metadata(candidate).get("selection_evidence")
    if not isinstance(evidence, dict):
        return {}
    selected_score = evidence.get("selected_score")
    selected_score = selected_score if isinstance(selected_score, dict) else {}
    compact_score = {
        "candidate_quality": _safe_float(selected_score.get("candidate_quality")),
        "high_fidelity": (
            selected_score.get("high_fidelity")
            if isinstance(selected_score.get("high_fidelity"), bool)
            else None
        ),
        "structure_similarity": _safe_float(selected_score.get("structure_similarity")),
        "structure_component_pass_rate": _safe_float(
            selected_score.get("structure_component_pass_rate")
        ),
        "token_budget": _safe_float(selected_score.get("token_budget")),
        "within_real_score_range": (
            selected_score.get("within_real_score_range")
            if isinstance(selected_score.get("within_real_score_range"), bool)
            else None
        ),
        "delta_from_min": _safe_float(selected_score.get("delta_from_min")),
        "baseline_pair_count": _safe_int(selected_score.get("baseline_pair_count")),
        "token_count": _safe_int(selected_score.get("token_count")),
    }
    result = {
        "schema_version": evidence.get("schema_version"),
        "selected_from_candidate_count": _safe_int(
            evidence.get("selected_from_candidate_count")
        ),
        "selected_input_index": _safe_int(evidence.get("selected_input_index")),
        "ranked_by": [
            str(item) for item in (evidence.get("ranked_by") or [])[:8] if item
        ],
        "selected_score": {
            key: value
            for key, value in compact_score.items()
            if value not in (None, "", [], {})
        },
        "selection_policy": _clip_text(
            evidence.get("selection_policy"),
            max_chars=160,
        ),
    }
    return {
        key: value for key, value in result.items() if value not in (None, "", [], {})
    }


def _candidate_has_arithmetic(candidate: dict[str, Any]) -> bool:
    return bool(_candidate_metadata(candidate).get("arithmetic_reconciliation"))


def _candidate_has_operation_evidence(candidate: dict[str, Any]) -> bool:
    metadata = _candidate_metadata(candidate)
    operation = _candidate_operation(candidate)
    if operation == "hard_negative":
        actual_label = str(metadata.get("actual_label") or "").strip()
        predicted_label = str(metadata.get("predicted_label") or "").strip()
        return bool(
            actual_label == "O" and predicted_label and predicted_label != actual_label
        )
    if operation == "add_line_item":
        return _candidate_is_grounded(candidate) and _candidate_has_arithmetic(
            candidate
        )
    if operation == "remove_line_item":
        removed = metadata.get("removed_item")
        return (
            isinstance(removed, dict)
            and removed.get("taxable") is False
            and _candidate_has_arithmetic(candidate)
        )
    if operation == "replace_field":
        field = metadata.get("mutable_field_evidence")
        return (
            isinstance(metadata.get("field_replacement"), dict)
            and isinstance(field, dict)
            and field.get("safe_to_mutate") is True
        )
    return False


def _rejection_merchant(record: dict[str, Any]) -> str:
    return str(record.get("merchant_name") or "Unknown merchant")


def _rejection_reason(record: dict[str, Any]) -> str:
    return str(record.get("reason") or "unknown")


def _count_by(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _next_synthesis_action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return _count_by(
        [
            str(action)
            for row in rows
            for action in row.get("next_synthesis_actions") or []
            if action
        ]
    )


def _normalized_entropy(counts: dict[str, int]) -> float | None:
    positive = [count for count in counts.values() if count > 0]
    total = sum(positive)
    if total <= 0:
        return None
    if len(positive) <= 1:
        return 0.0
    entropy = -sum((count / total) * math.log(count / total) for count in positive)
    return round(entropy / math.log(len(positive)), 3)


def _top_count_share(counts: dict[str, int]) -> tuple[str | None, int, float | None]:
    positive = {key: count for key, count in counts.items() if count > 0}
    total = sum(positive.values())
    if total <= 0:
        return None, 0, None
    key, count = max(positive.items(), key=lambda item: (item[1], item[0]))
    return key, count, round(count / total, 3)


def _balance_risk(
    *,
    accepted_count: int,
    merchant_count: int,
    operation_count: int,
    top_merchant_share: float | None,
    top_operation_share: float | None,
) -> tuple[str, list[str]]:
    if accepted_count <= 0:
        return "none", ["no_accepted_synthetic_examples"]
    if accepted_count < 3:
        return "low", ["too_few_examples_for_balance_assessment"]

    level = "low"
    reasons: list[str] = []
    if merchant_count <= 1:
        level = "high"
        reasons.append("single_merchant_accepted")
    elif top_merchant_share is not None and top_merchant_share >= 0.80:
        level = "high"
        reasons.append("top_merchant_share_ge_80pct")
    elif top_merchant_share is not None and top_merchant_share >= 0.67:
        level = "medium"
        reasons.append("top_merchant_share_ge_67pct")

    if operation_count <= 1:
        if level != "high":
            level = "medium"
        reasons.append("single_operation_accepted")
    elif top_operation_share is not None and top_operation_share >= 0.80:
        if level != "high":
            level = "medium"
        reasons.append("top_operation_share_ge_80pct")
    elif top_operation_share is not None and top_operation_share >= 0.67:
        if level == "low":
            level = "medium"
        reasons.append("top_operation_share_ge_67pct")

    return level, reasons


def _accepted_mix_balance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    merchant_counts = _count_by([_candidate_merchant(row) for row in rows])
    operation_counts = _count_by([_candidate_operation(row) for row in rows])
    top_merchant, top_merchant_count, top_merchant_share = _top_count_share(
        merchant_counts
    )
    top_operation, top_operation_count, top_operation_share = _top_count_share(
        operation_counts
    )
    accepted_count = len(rows)
    risk_level, risk_reasons = _balance_risk(
        accepted_count=accepted_count,
        merchant_count=len(merchant_counts),
        operation_count=len(operation_counts),
        top_merchant_share=top_merchant_share,
        top_operation_share=top_operation_share,
    )
    return {
        "accepted_count": accepted_count,
        "merchant_count": len(merchant_counts),
        "operation_count": len(operation_counts),
        "top_merchant": top_merchant,
        "top_merchant_count": top_merchant_count,
        "top_merchant_share": top_merchant_share,
        "top_operation": top_operation,
        "top_operation_count": top_operation_count,
        "top_operation_share": top_operation_share,
        "merchant_entropy": _normalized_entropy(merchant_counts),
        "operation_entropy": _normalized_entropy(operation_counts),
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
    }


def _synthetic_training_batch_policy(
    *,
    candidate_mix: dict[str, Any],
    selected_rows: list[dict[str, Any]],
    max_per_merchant: int,
    max_per_merchant_operation: int,
    bundle_reasons: list[str],
) -> dict[str, Any]:
    """Return a conservative policy for the next train-only synthetic batch."""
    reported_accepted_count = _safe_int(candidate_mix.get("accepted_count")) or 0
    selected_count = len(selected_rows)
    available_count = min(reported_accepted_count, selected_count)
    balance = candidate_mix.get("accepted_mix_balance") or {}
    risk_level = str(balance.get("risk_level") or "none").strip().lower()
    risk_reasons = [str(reason) for reason in balance.get("risk_reasons") or []]
    candidate_quality_count = sum(1 for row in selected_rows if _candidate_quality(row))
    high_fidelity_count = sum(
        1
        for row in selected_rows
        if (_candidate_quality(row) or {}).get("high_fidelity") is True
    )
    hold_reasons = list(dict.fromkeys(str(reason) for reason in bundle_reasons))
    if available_count <= 0 and "no_accepted_synthetic_examples" not in hold_reasons:
        hold_reasons.append("no_accepted_synthetic_examples")
    if reported_accepted_count != selected_count:
        hold_reasons.append("accepted_selected_count_mismatch")
    if risk_level in {"medium", "high"}:
        hold_reasons.append("rebalance_synthetic_mix_before_training")
    if available_count > 0 and candidate_quality_count < available_count:
        hold_reasons.append("missing_candidate_quality_assessment")
    elif available_count > 0 and high_fidelity_count < available_count:
        hold_reasons.append("no_high_fidelity_candidate_quality")
    hold_reasons = list(dict.fromkeys(hold_reasons))

    status = "bounded_augmentation"
    recommended_count = available_count
    max_synthetic_train_share = 0.05
    if hold_reasons:
        status = "hold"
        recommended_count = 0
        max_synthetic_train_share = 0.0
    elif available_count < 3:
        status = "smoke_test_only"
        recommended_count = available_count
        max_synthetic_train_share = 0.01
    elif high_fidelity_count:
        recommended_count = min(available_count, high_fidelity_count)

    return {
        "schema_version": "synthetic-training-batch-policy-v1",
        "status": status,
        "recommended_example_count": recommended_count,
        "accepted_candidate_count": reported_accepted_count,
        "selected_candidate_count": selected_count,
        "candidate_quality_count": candidate_quality_count,
        "high_fidelity_candidate_count": high_fidelity_count,
        "max_synthetic_train_share": max_synthetic_train_share,
        "max_per_merchant": max_per_merchant,
        "max_per_merchant_operation": max_per_merchant_operation,
        "overtraining_risk_level": risk_level,
        "risk_reasons": risk_reasons[:8],
        "hold_reasons": hold_reasons[:8],
        "requires_real_validation_split": True,
        "review_required": status != "bounded_augmentation",
    }


def _compact_llm_execution(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"mode": "unknown"}
    result: dict[str, Any] = {
        "mode": str(value.get("mode") or "unknown"),
        "paid_llm_disabled": (
            value.get("paid_llm_disabled")
            if isinstance(value.get("paid_llm_disabled"), bool)
            else None
        ),
        "api_call_allowed": (
            value.get("api_call_allowed")
            if isinstance(value.get("api_call_allowed"), bool)
            else None
        ),
        "configured_model": value.get("configured_model"),
        "model_profile": value.get("model_profile"),
        "latest_openai_model": value.get("latest_openai_model"),
        "latest_model_source": value.get("latest_model_source"),
        "latest_model_verified_at": value.get("latest_model_verified_at"),
    }
    return {key: item for key, item in result.items() if item not in (None, "")}


def _artifact_llm_execution(artifact: dict[str, Any]) -> dict[str, Any]:
    trace = artifact.get("_trace_metadata")
    if isinstance(trace, dict):
        execution = trace.get("llm_execution")
        if isinstance(execution, dict):
            return _compact_llm_execution(execution)
    execution = artifact.get("llm_execution")
    return _compact_llm_execution(execution)


def _summarize_llm_execution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    executions = [row.get("llm_execution") or {"mode": "unknown"} for row in rows]
    latest_sources = sorted(
        {
            str(execution.get("latest_model_source"))
            for execution in executions
            if execution.get("latest_model_source")
        }
    )
    configured_models = sorted(
        {
            str(execution.get("configured_model"))
            for execution in executions
            if execution.get("configured_model")
        }
    )
    latest_openai_models = sorted(
        {
            str(execution.get("latest_openai_model"))
            for execution in executions
            if execution.get("latest_openai_model")
        }
    )
    verified_dates = sorted(
        {
            str(execution.get("latest_model_verified_at"))
            for execution in executions
            if execution.get("latest_model_verified_at")
        }
    )
    result = {
        "mode_counts": _count_by(
            [str(execution.get("mode") or "unknown") for execution in executions]
        ),
        "paid_llm_disabled_count": sum(
            1 for execution in executions if execution.get("paid_llm_disabled") is True
        ),
        "api_call_allowed_count": sum(
            1 for execution in executions if execution.get("api_call_allowed") is True
        ),
        "configured_models": configured_models[:5],
        "latest_openai_models": latest_openai_models[:3],
        "latest_model_sources": latest_sources[:3],
        "latest_model_verified_at": verified_dates[-1] if verified_dates else None,
    }
    return {key: value for key, value in result.items() if value not in (None, [])}


def _model_freshness_check_date() -> date:
    raw_value = os.environ.get(LLM_MODEL_FRESHNESS_CHECK_DATE_ENV)
    if raw_value:
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            pass
    return date.today()


def _llm_model_freshness_gate(llm_execution: dict[str, Any]) -> dict[str, Any]:
    """Summarize whether paid LLM synthesis used current model guidance."""
    verified_at = str(llm_execution.get("latest_model_verified_at") or "")
    source_values = [
        str(value) for value in llm_execution.get("latest_model_sources") or [] if value
    ]
    latest_openai_models = [
        str(value)
        for value in llm_execution.get("latest_openai_models") or []
        if value
    ]
    api_call_allowed_count = _safe_int(llm_execution.get("api_call_allowed_count")) or 0
    mode_counts = llm_execution.get("mode_counts")
    llm_assisted_mode_count = (
        sum(
            count
            for mode, raw_count in mode_counts.items()
            if str(mode) not in {"deterministic_fallback", "unknown"}
            and (count := (_safe_int(raw_count) or 0)) > 0
        )
        if isinstance(mode_counts, dict)
        else 0
    )
    requires_current_guidance = (
        api_call_allowed_count > 0 or llm_assisted_mode_count > 0
    )
    parsed_date: date | None = None
    if verified_at:
        try:
            parsed_date = date.fromisoformat(verified_at)
        except ValueError:
            parsed_date = None
    raw_age_days = (
        max(0, (_model_freshness_check_date() - parsed_date).days)
        if parsed_date
        else None
    )
    fresh = (
        raw_age_days is not None and raw_age_days <= LLM_MODEL_FRESHNESS_MAX_AGE_DAYS
    )
    has_source = bool(source_values)
    passed = (fresh and has_source) or not requires_current_guidance
    result = {
        "enabled": True,
        "passed": passed,
        "requires_current_model_guidance": requires_current_guidance,
        "api_call_allowed_count": api_call_allowed_count,
        "llm_assisted_mode_count": (
            llm_assisted_mode_count if llm_assisted_mode_count else None
        ),
        "latest_model_verified_at": verified_at or None,
        "latest_model_age_days": raw_age_days if requires_current_guidance else None,
        "max_age_days": LLM_MODEL_FRESHNESS_MAX_AGE_DAYS,
        "latest_openai_models": latest_openai_models[:3],
        "latest_model_sources": source_values[:3],
        "reason": None if passed else "latest_model_guidance_stale_or_missing",
    }
    return {key: value for key, value in result.items() if value not in (None, [])}


def _compact_mutation_inventory(
    readiness: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    operation_counts = _count_by([_candidate_operation(row) for row in candidates])
    category_counts = _count_by(
        [
            _candidate_category(row)
            for row in candidates
            if _candidate_category(row) != "unknown"
        ]
    )
    field_replacement_counts = _count_by(
        [
            label
            for row in candidates
            if (label := _candidate_field_replacement_label(row))
        ]
    )
    return {
        "supported_operations": list(readiness.get("supported_operations") or []),
        "candidate_operation_counts": operation_counts,
        "candidate_category_counts": category_counts,
        "field_replacement_counts": field_replacement_counts,
        "hard_negative_labels": list(readiness.get("ready_hard_negative_labels") or [])[
            :8
        ],
        "grounded_add_item_candidate_count": _safe_int(
            readiness.get("grounded_add_item_candidate_count")
        )
        or 0,
        "removable_item_candidate_count": _safe_int(
            readiness.get("removable_item_candidate_count")
        )
        or 0,
        "mutable_field_count": _safe_int(readiness.get("mutable_field_count")) or 0,
        "mutable_fields": readiness.get("mutable_fields") or {},
        "grounded_add_item_examples": list(
            readiness.get("grounded_add_item_examples") or []
        )[:5],
    }


SYNTHESIS_OPERATION_ORDER = (
    "hard_negative",
    "add_line_item",
    "remove_line_item",
    "replace_field",
    "compose_online_catalog",
)

SYNTHESIS_ACTION_READY_STATUSES = {"ready"}


def _operation_readiness_row(
    operation: str,
    readiness: dict[str, Any],
    operation_counts: dict[str, int],
    operation_evidence_counts: dict[str, int],
    *,
    readiness_status: str,
) -> dict[str, Any]:
    supported = set(readiness.get("supported_operations") or [])
    supported_for_operation = operation in supported
    candidate_count = _safe_int(operation_counts.get(operation)) or 0
    evidence_candidate_count = _safe_int(operation_evidence_counts.get(operation)) or 0
    blockers: list[str] = []
    evidence: dict[str, Any] = {}
    merchant_actionable = readiness_status in SYNTHESIS_ACTION_READY_STATUSES
    contract_capacity_count = 0

    if operation == "hard_negative":
        labels = list(readiness.get("ready_hard_negative_labels") or [])[:8]
        label_count = _safe_int(readiness.get("hard_negative_label_count")) or 0
        contract_capacity_count = label_count
        has_capacity = bool(labels or label_count > 0)
        ready = supported_for_operation and has_capacity
        evidence.update(
            {
                "labels": labels,
                "hard_negative_label_count": label_count,
            }
        )
        if not has_capacity:
            blockers.append("no_supported_hard_negative_slots")
    elif operation == "add_line_item":
        grounded_count = (
            _safe_int(readiness.get("grounded_add_item_candidate_count")) or 0
        )
        contract_capacity_count = grounded_count
        ready = supported_for_operation and grounded_count > 0
        evidence.update(
            {
                "grounded_candidate_count": grounded_count,
                "grounded_examples": list(
                    readiness.get("grounded_add_item_examples") or []
                )[:5],
            }
        )
        if grounded_count <= 0:
            blockers.append("no_cross_receipt_grounded_add_items")
    elif operation == "remove_line_item":
        removable_count = (
            _safe_int(readiness.get("removable_item_candidate_count")) or 0
        )
        contract_capacity_count = removable_count
        ready = supported_for_operation and removable_count > 0
        evidence["removable_item_candidate_count"] = removable_count
        if removable_count <= 0:
            blockers.append("no_removable_non_taxable_items")
    elif operation == "replace_field":
        mutable_count = _safe_int(readiness.get("mutable_field_count")) or 0
        mutable_fields = readiness.get("mutable_fields") or {}
        contract_capacity_count = mutable_count
        ready = supported_for_operation and mutable_count > 0
        evidence.update(
            {
                "mutable_field_count": mutable_count,
                "mutable_fields": mutable_fields,
            }
        )
        if mutable_count <= 0:
            blockers.append("no_stable_mutable_fields")
    elif operation == "compose_online_catalog":
        compose_count = (
            _safe_int(readiness.get("compose_online_catalog_candidate_count")) or 0
        )
        contract_capacity_count = compose_count
        ready = supported_for_operation and compose_count > 0
        evidence.update({"online_catalog_item_count": compose_count})
        if compose_count <= 0:
            blockers.append("no_online_catalog_with_stable_tax")
    else:
        ready = False

    if not supported_for_operation and contract_capacity_count > 0:
        blockers.append("operation_not_supported_by_contract")

    if not merchant_actionable:
        ready = False
        blockers = list(
            dict.fromkeys(
                [
                    f"readiness_status_{readiness_status or 'missing'}",
                    *[str(item) for item in readiness.get("blockers") or []],
                    *blockers,
                ]
            )
        )

    return {
        "operation": operation,
        "ready": ready,
        "supported": operation in supported,
        "candidate_count": candidate_count,
        "evidence_candidate_count": evidence_candidate_count,
        "evidence": evidence,
        "blockers": blockers,
    }


def _operation_readiness_matrix(
    readiness: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    operation_counts = _count_by([_candidate_operation(row) for row in candidates])
    operation_evidence_counts = _count_by(
        [
            _candidate_operation(row)
            for row in candidates
            if _candidate_has_operation_evidence(row)
        ]
    )
    readiness_status = str(readiness.get("status") or "missing").strip().lower()
    # compose_online_catalog is an OPT-IN operation: it only applies to merchants
    # with a registered/injected online catalog. For everyone else it is not
    # "missing" (you can't mine it into existence), so omit its readiness row
    # unless the merchant actually has online-catalog capacity or composed rows.
    compose_applicable = (
        (_safe_int(readiness.get("compose_online_catalog_candidate_count")) or 0) > 0
        or (_safe_int(operation_counts.get("compose_online_catalog")) or 0) > 0
    )
    operations = [
        operation
        for operation in SYNTHESIS_OPERATION_ORDER
        if operation != "compose_online_catalog" or compose_applicable
    ]
    return [
        _operation_readiness_row(
            operation,
            readiness,
            operation_counts,
            operation_evidence_counts,
            readiness_status=readiness_status,
        )
        for operation in operations
    ]


def _next_synthesis_actions(
    operation_readiness: list[dict[str, Any]],
    *,
    readiness_status: str,
    source_quality: dict[str, Any] | None = None,
) -> list[str]:
    actions: list[str] = []
    if _source_quality_has_recoverable_unlabeled_text(source_quality or {}):
        actions.append("validate_recoverable_unlabeled_receipts")
    if readiness_status == "blocked":
        actions.append("resolve_merchant_synthesis_blockers")
        return actions

    rows_by_operation = {str(row.get("operation")): row for row in operation_readiness}
    for operation in SYNTHESIS_OPERATION_ORDER:
        row = rows_by_operation.get(operation) or {}
        if (
            row.get("ready")
            and (_safe_int(row.get("evidence_candidate_count")) or 0) > 0
        ):
            actions.append(f"synthesize_{operation}_from_existing_evidence")
        elif row.get("ready"):
            actions.append(f"generate_{operation}_candidate_from_ready_contract")
        elif operation == "hard_negative":
            actions.append("mine_confusion_targets_for_hard_negative_slots")
        elif operation == "add_line_item":
            actions.append("collect_cross_receipt_item_and_category_evidence")
        elif operation == "remove_line_item":
            actions.append("collect_multi_item_non_taxable_receipts_with_totals")
        elif operation == "replace_field":
            actions.append("collect_stable_date_time_examples_for_field_replacement")
    return actions


def summarize_merchant_synthesis_audit(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a compact, local merchant audit for safe synthesis decisions."""
    audits: list[dict[str, Any]] = []
    for artifact in artifacts:
        profile = artifact.get("merchant_receipt_parameterization") or {}
        readiness = profile.get("synthesis_readiness") or {}
        candidates = [
            row
            for row in artifact.get("synthetic_receipt_candidates") or []
            if isinstance(row, dict)
        ]
        merchant = str(
            artifact.get("merchant_name")
            or profile.get("merchant_name")
            or "Unknown merchant"
        )
        readiness_status = str(readiness.get("status") or "missing")
        operation_readiness = _operation_readiness_matrix(
            readiness,
            candidates,
        )
        missing_operations = [
            str(row["operation"])
            for row in operation_readiness
            if row.get("ready") is not True
        ]
        audits.append(
            {
                "merchant_name": merchant,
                "readiness_status": readiness_status,
                "readiness_score": _safe_float(readiness.get("score")),
                "source_receipt_count": _safe_int(artifact.get("source_receipt_count"))
                or _safe_int(profile.get("receipt_count"))
                or 0,
                "analyzed_receipt_count": _safe_int(
                    readiness.get("analyzed_receipt_count")
                ),
                "line_item_count": _safe_int(readiness.get("line_item_count")),
                "catalog_item_count": _safe_int(readiness.get("catalog_item_count"))
                or len(profile.get("observed_item_catalog") or []),
                "cross_receipt_catalog_item_count": _safe_int(
                    readiness.get("cross_receipt_catalog_item_count")
                ),
                "category_count": _safe_int(readiness.get("category_count")),
                "candidate_count": len(candidates),
                "grounded_candidate_count": sum(
                    1 for row in candidates if _candidate_is_grounded(row)
                ),
                "arithmetic_candidate_count": sum(
                    1 for row in candidates if _candidate_has_arithmetic(row)
                ),
                "mutation_inventory": _compact_mutation_inventory(
                    readiness,
                    candidates,
                ),
                "operation_readiness": operation_readiness,
                "missing_operations": missing_operations,
                "next_synthesis_actions": _next_synthesis_actions(
                    operation_readiness,
                    readiness_status=readiness_status.strip().lower(),
                    source_quality=_artifact_source_quality(artifact),
                ),
                "llm_execution": _artifact_llm_execution(artifact),
                "safe_training_policy": {
                    "validation_policy": (
                        (profile.get("generation_limits") or {}).get("validation_split")
                        or "real_receipts_only"
                    ),
                    "max_candidates_per_training_run": _safe_int(
                        (profile.get("generation_limits") or {}).get(
                            "max_candidates_per_training_run"
                        )
                    ),
                    "candidate_requires_train_only": True,
                },
                "blockers": list(readiness.get("blockers") or [])[:8],
                "limitations": list(readiness.get("limitations") or [])[:8],
            }
        )
    return audits[:50]


def _compact_catalog_contract_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_text": item.get("product_text"),
        "category": item.get("category"),
        "line_total": item.get("line_total"),
        "observed_count": _safe_int(item.get("observed_count"))
        or _safe_int(item.get("count")),
        "source_receipt_count": _safe_int(item.get("source_receipt_count"))
        or len(item.get("source_receipt_keys") or []),
    }


def _compact_category_contract(
    category_patterns: dict[str, Any],
) -> dict[str, Any]:
    heading_counts = category_patterns.get("heading_counts") or {}
    if not isinstance(heading_counts, dict):
        heading_counts = {}
    top_items = category_patterns.get("top_items_by_category") or {}
    if not isinstance(top_items, dict):
        top_items = {}
    return {
        "heading_counts": {
            str(category): count
            for category, value in heading_counts.items()
            if (count := _safe_int(value)) is not None and count > 0
        },
        "top_items_by_category": {
            str(category): [
                {
                    "product_text": item.get("product_text"),
                    "count": _safe_int(item.get("count")),
                }
                for item in (items or [])[:5]
                if isinstance(item, dict)
            ]
            for category, items in list(top_items.items())[:8]
        },
    }


def _compact_mutable_field_contract(
    readiness: dict[str, Any],
) -> dict[str, Any]:
    fields = readiness.get("mutable_fields") or {}
    if not isinstance(fields, dict):
        return {}
    return {
        str(label): {
            "label": field.get("label") or label,
            "safe_to_mutate": field.get("safe_to_mutate") is True,
            "stable_format": field.get("stable_format"),
            "stable_geometry": field.get("stable_geometry") is True,
            "observed_count": _safe_int(field.get("observed_count")),
            "examples": list(field.get("examples") or [])[:5],
            "format_counts": {
                str(format_name): count
                for format_name, value in (field.get("format_counts") or {}).items()
                if (count := _safe_int(value)) is not None and count > 0
            },
            "mutation_strategy": field.get("mutation_strategy"),
        }
        for label, field in fields.items()
        if isinstance(field, dict)
    }


def _compact_tax_contract(readiness: dict[str, Any]) -> dict[str, Any]:
    policy = readiness.get("tax_policy") or {}
    if not isinstance(policy, dict):
        return {}
    if not policy:
        return {}
    result = {
        "supported_policy": policy.get("supported_policy"),
        "taxable_item_count": _safe_int(policy.get("taxable_item_count")),
        "non_taxable_item_count": _safe_int(policy.get("non_taxable_item_count")),
        "receipts_with_tax_total": _safe_int(policy.get("receipts_with_tax_total")),
        "receipts_with_taxable_items": _safe_int(
            policy.get("receipts_with_taxable_items")
        ),
        "tax_rate_observation_count": _safe_int(
            policy.get("tax_rate_observation_count")
        ),
        "stable_tax_rate": policy.get("stable_tax_rate") is True,
        "avg_tax_rate": policy.get("avg_tax_rate"),
        "avg_tax_rate_percent": policy.get("avg_tax_rate_percent"),
        "tax_changing_synthesis_ready": (
            policy.get("tax_changing_synthesis_ready") is True
        ),
        "tax_changing_synthesis_blockers": list(
            policy.get("tax_changing_synthesis_blockers") or []
        )[:8],
    }
    return {
        key: value for key, value in result.items() if value not in (None, "", [], {})
    }


def _merchant_mix_lookup(candidate_mix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = candidate_mix.get("merchants") or []
    return {
        str(row.get("merchant_name")): row
        for row in rows
        if isinstance(row, dict) and row.get("merchant_name")
    }


def _artifact_source_quality(artifact: dict[str, Any]) -> dict[str, Any]:
    value = artifact.get("source_receipt_quality")
    return value if isinstance(value, dict) else {}


def _source_quality_status(source_quality: dict[str, Any]) -> str:
    return str(source_quality.get("status") or "").strip().lower()


def _source_quality_blockers(source_quality: dict[str, Any]) -> list[str]:
    if not source_quality:
        return []
    blockers = [str(item) for item in source_quality.get("blockers") or [] if item]
    if _source_quality_status(source_quality) == "blocked":
        blockers.insert(0, "source_receipt_quality_blocked")
    return list(dict.fromkeys(blockers))[:8]


def _source_quality_limitations(source_quality: dict[str, Any]) -> list[str]:
    if not source_quality:
        return []
    limitations = [
        str(item) for item in source_quality.get("limitations") or [] if item
    ]
    if "unlabeled_text_requires_label_validation" in limitations:
        limitations.insert(0, "unlabeled_text_requires_label_validation")
    if _source_quality_status(source_quality) == "limited":
        limitations.insert(0, "source_receipt_quality_limited")
    return list(dict.fromkeys(limitations))[:8]


def _source_quality_effective_status(
    readiness_status: str,
    source_quality: dict[str, Any],
) -> str:
    if _source_quality_status(source_quality) == "blocked":
        return "blocked"
    return readiness_status


def _source_quality_operation_blocker(
    source_quality: dict[str, Any],
    operation: str,
) -> str | None:
    if not source_quality:
        return None
    if _source_quality_status(source_quality) == "blocked":
        return "source_receipt_quality_blocked"

    limitations = {
        str(item)
        for item in source_quality.get("limitations") or []
        if str(item).strip()
    }
    line_item_receipts = _safe_int(source_quality.get("receipts_with_line_item_labels"))
    grand_total_receipts = _safe_int(
        source_quality.get("receipts_with_grand_total_label")
    )
    date_time_receipts = _safe_int(
        source_quality.get("receipts_with_date_or_time_label")
    )
    receipt_count = _safe_int(source_quality.get("receipt_count")) or 0

    if operation in {"add_line_item", "remove_line_item"}:
        if "no_labeled_line_items" in limitations or line_item_receipts == 0:
            return "source_quality_no_labeled_line_items"
        if "no_grand_total_labels" in limitations or grand_total_receipts == 0:
            return "source_quality_no_grand_total_labels"
    if operation == "add_line_item" and (
        "single_receipt_limits_cross_receipt_grounding" in limitations
        or receipt_count < 2
    ):
        return "source_quality_single_receipt_grounding"
    if operation == "replace_field" and date_time_receipts == 0:
        return "source_quality_no_date_time_labels"
    return None


def _source_quality_operation_blockers(
    source_quality: dict[str, Any],
) -> dict[str, str]:
    return {
        operation: blocker
        for operation in SYNTHESIS_OPERATION_FAMILIES
        if (blocker := _source_quality_operation_blocker(source_quality, operation))
    }


def _compact_source_quality_contract(
    source_quality: dict[str, Any],
) -> dict[str, Any]:
    if not source_quality:
        return {}
    return {
        key: value
        for key, value in {
            "status": source_quality.get("status"),
            "receipt_count": _safe_int(source_quality.get("receipt_count")),
            "receipts_with_labels": _safe_int(
                source_quality.get("receipts_with_labels")
            ),
            "receipts_with_line_item_labels": _safe_int(
                source_quality.get("receipts_with_line_item_labels")
            ),
            "receipts_with_grand_total_label": _safe_int(
                source_quality.get("receipts_with_grand_total_label")
            ),
            "receipts_with_date_or_time_label": _safe_int(
                source_quality.get("receipts_with_date_or_time_label")
            ),
            "labeled_word_count": _safe_int(source_quality.get("labeled_word_count")),
            "text_structure_status": source_quality.get("text_structure_status"),
            "line_item_like_text_line_count": _safe_int(
                source_quality.get("line_item_like_text_line_count")
            ),
            "total_like_text_line_count": _safe_int(
                source_quality.get("total_like_text_line_count")
            ),
            "blockers": _source_quality_blockers(source_quality),
            "limitations": _source_quality_limitations(source_quality),
        }.items()
        if value not in (None, "", [], {})
    }


def _source_quality_has_recoverable_unlabeled_text(
    source_quality: dict[str, Any],
) -> bool:
    if not isinstance(source_quality, dict) or not source_quality:
        return False
    if source_quality.get("text_structure_status") == "recoverable_unlabeled_text":
        return True
    return "unlabeled_text_requires_label_validation" in set(
        source_quality.get("limitations") or []
    )


def build_merchant_synthesis_contracts(
    artifacts: list[dict[str, Any]],
    *,
    candidate_mix: dict[str, Any] | None = None,
    min_structure_similarity: float = 0.6,
    structure_component_thresholds: dict[str, float] | None = None,
    max_per_merchant: int = 5,
    max_per_merchant_operation: int = 2,
) -> list[dict[str, Any]]:
    """Build merchant-level contracts that explain safe synthesis mutations."""
    mix_by_merchant = _merchant_mix_lookup(candidate_mix or {})
    structure_component_thresholds = structure_component_thresholds or {}
    contracts: list[dict[str, Any]] = []
    for artifact in artifacts:
        profile = artifact.get("merchant_receipt_parameterization") or {}
        readiness = profile.get("synthesis_readiness") or {}
        merchant = _artifact_merchant(artifact)
        generation_limits = profile.get("generation_limits") or {}
        catalog = [
            item
            for item in profile.get("observed_item_catalog") or []
            if isinstance(item, dict)
        ]
        mix = mix_by_merchant.get(merchant, {})
        supported_operations = list(readiness.get("supported_operations") or [])
        supported_operation_set = set(supported_operations)
        source_quality = _artifact_source_quality(artifact)
        source_quality_operation_blockers = _source_quality_operation_blockers(
            source_quality
        )
        hard_negative_label_count = (
            _safe_int(readiness.get("hard_negative_label_count")) or 0
        )
        grounded_add_count = (
            _safe_int(readiness.get("grounded_add_item_candidate_count")) or 0
        )
        removable_item_count = (
            _safe_int(readiness.get("removable_item_candidate_count")) or 0
        )
        mutable_field_count = _safe_int(readiness.get("mutable_field_count")) or 0
        compose_online_catalog_count = (
            _safe_int(readiness.get("compose_online_catalog_candidate_count")) or 0
        )
        contract = {
            "merchant_name": merchant,
            "status": _source_quality_effective_status(
                str(readiness.get("status") or "missing"),
                source_quality,
            ),
            "score": _safe_float(readiness.get("score")),
            "source_receipt_count": _safe_int(profile.get("receipt_count"))
            or _safe_int(artifact.get("source_receipt_count"))
            or 0,
            "source_receipt_keys": list(profile.get("source_receipt_keys") or [])[:8],
            "source_receipt_quality": _compact_source_quality_contract(source_quality),
            "supported_operations": supported_operations[:8],
            "operation_contracts": {
                "hard_negative": {
                    "ready": (
                        "hard_negative" in supported_operation_set
                        and bool(
                            readiness.get("ready_hard_negative_labels")
                            or hard_negative_label_count > 0
                        )
                    )
                    and "hard_negative" not in source_quality_operation_blockers,
                    "source_quality_blocker": source_quality_operation_blockers.get(
                        "hard_negative"
                    ),
                    "labels": list(readiness.get("ready_hard_negative_labels") or [])[
                        :8
                    ],
                },
                "add_line_item": {
                    "ready": (
                        "add_line_item" in supported_operation_set
                        and grounded_add_count > 0
                    )
                    and "add_line_item" not in source_quality_operation_blockers,
                    "source_quality_blocker": source_quality_operation_blockers.get(
                        "add_line_item"
                    ),
                    "candidate_count": grounded_add_count,
                    "requires": [
                        "item_seen_in_other_receipt",
                        "base_receipt_has_category",
                        "non_taxable_arithmetic_reconciliation",
                    ],
                },
                "remove_line_item": {
                    "ready": (
                        "remove_line_item" in supported_operation_set
                        and removable_item_count > 0
                    )
                    and "remove_line_item" not in source_quality_operation_blockers,
                    "source_quality_blocker": source_quality_operation_blockers.get(
                        "remove_line_item"
                    ),
                    "candidate_count": removable_item_count,
                    "requires": [
                        "removable_non_taxable_item",
                        "summary_total_reconciliation",
                    ],
                },
                "replace_field": {
                    "ready": (
                        "replace_field" in supported_operation_set
                        and mutable_field_count > 0
                    )
                    and "replace_field" not in source_quality_operation_blockers,
                    "source_quality_blocker": source_quality_operation_blockers.get(
                        "replace_field"
                    ),
                    "candidate_count": mutable_field_count,
                    "fields": _compact_mutable_field_contract(readiness),
                    "requires": [
                        "stable_format",
                        "stable_geometry",
                        "multiple_observed_values",
                    ],
                },
            },
            "category_contract": _compact_category_contract(
                profile.get("category_patterns") or {}
            ),
            "catalog_contract": {
                "catalog_item_count": _safe_int(readiness.get("catalog_item_count"))
                or len(catalog),
                "cross_receipt_catalog_item_count": _safe_int(
                    readiness.get("cross_receipt_catalog_item_count")
                ),
                "grounded_add_item_examples": list(
                    readiness.get("grounded_add_item_examples") or []
                )[:5],
                "top_catalog_items": [
                    _compact_catalog_contract_item(item) for item in catalog[:8]
                ],
            },
            "bundle_acceptance": {
                "candidate_count": _safe_int(mix.get("candidate_count")),
                "accepted_count": _safe_int(mix.get("accepted_count")),
                "rejected_count": _safe_int(mix.get("rejected_count")),
                "accepted_operation_counts": mix.get("accepted_operation_counts") or {},
                "accepted_category_counts": mix.get("accepted_category_counts") or {},
                "accepted_field_replacement_counts": mix.get(
                    "accepted_field_replacement_counts"
                )
                or {},
                "rejection_reasons": mix.get("rejection_reasons") or {},
            },
            "quality_gates": {
                "validation_policy": (
                    generation_limits.get("validation_split") or "real_receipts_only"
                ),
                "candidate_requires_train_only": True,
                "min_structure_similarity": min_structure_similarity,
                "max_per_merchant": max_per_merchant,
                "max_per_merchant_operation": max_per_merchant_operation,
                "max_candidates_per_training_run": _safe_int(
                    generation_limits.get("max_candidates_per_training_run")
                ),
                "structure_component_thresholds": structure_component_thresholds,
            },
            "blockers": list(
                dict.fromkeys(
                    list(readiness.get("blockers") or [])
                    + _source_quality_blockers(source_quality)
                )
            )[:8],
            "limitations": list(
                dict.fromkeys(
                    list(readiness.get("limitations") or [])
                    + _source_quality_limitations(source_quality)
                )
            )[:8],
        }
        # compose_online_catalog is opt-in: only emit its operation contract for
        # merchants that actually have online-catalog capacity, so catalog-less
        # merchants keep a clean contract.
        if compose_online_catalog_count > 0:
            contract["operation_contracts"]["compose_online_catalog"] = {
                "ready": (
                    "compose_online_catalog" in supported_operation_set
                    and compose_online_catalog_count > 0
                )
                and "compose_online_catalog"
                not in source_quality_operation_blockers,
                "source_quality_blocker": source_quality_operation_blockers.get(
                    "compose_online_catalog"
                ),
                "candidate_count": compose_online_catalog_count,
                "requires": [
                    "online_catalog_grounded_rows",
                    "self_assigned_item_labels",
                    "stable_observed_tax_rate",
                ],
            }
        if not contract["source_receipt_quality"]:
            contract.pop("source_receipt_quality", None)
        for operation, operation_contract in (
            contract.get("operation_contracts") or {}
        ).items():
            if isinstance(operation_contract, dict):
                blocker = source_quality_operation_blockers.get(str(operation))
                if blocker:
                    operation_contract["source_quality_blocker"] = blocker
                else:
                    operation_contract.pop("source_quality_blocker", None)
        tax_contract = _compact_tax_contract(readiness)
        if tax_contract:
            contract["tax_contract"] = tax_contract
        contracts.append(contract)
    return contracts[:50]


def _score_summary(scores: list[float]) -> dict[str, float | int | None]:
    if not scores:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {
        "count": len(scores),
        "avg": round(sum(scores) / len(scores), 3),
        "min": round(min(scores), 3),
        "max": round(max(scores), 3),
    }


def _component_score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values_by_component: dict[str, list[float]] = {}
    for row in rows:
        for component, value in _candidate_structure_components(row).items():
            values_by_component.setdefault(component, []).append(value)
    return {
        component: _score_summary(values)
        for component, values in sorted(values_by_component.items())
    }


def _candidate_quality_component_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values_by_component: dict[str, list[float]] = {}
    for row in rows:
        for component, value in _candidate_quality_components(row).items():
            values_by_component.setdefault(component, []).append(value)
    return {
        component: _score_summary(values)
        for component, values in sorted(values_by_component.items())
    }


def _real_baseline_comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    comparisons = [
        comparison
        for row in rows
        if (comparison := _candidate_real_baseline_comparison(row))
    ]
    if not comparisons:
        return {"count": 0}

    within_count = sum(
        1 for comparison in comparisons if comparison.get("within_real_score_range")
    )

    def values_for(key: str) -> list[float]:
        return [
            value
            for comparison in comparisons
            if (value := _safe_float(comparison.get(key))) is not None
        ]

    return {
        "count": len(comparisons),
        "within_real_score_range_count": within_count,
        "below_real_score_range_count": len(comparisons) - within_count,
        "within_real_score_range_share": round(within_count / len(comparisons), 3),
        "candidate_score": _score_summary(values_for("candidate_score")),
        "baseline_avg": _score_summary(values_for("baseline_avg")),
        "baseline_min": _score_summary(values_for("baseline_min")),
        "baseline_pair_count": _score_summary(values_for("baseline_pair_count")),
        "delta_from_avg": _score_summary(values_for("delta_from_avg")),
        "delta_from_min": _score_summary(values_for("delta_from_min")),
    }


def _accepted_source_lineage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    lineages = [_candidate_source_lineage(row) for row in rows]
    flags = [
        lineage.get("evidence_flags") or {}
        for lineage in lineages
        if isinstance(lineage, dict)
    ]
    source_keys = _unique_source_keys(
        *[
            lineage.get("source_receipt_keys") or []
            for lineage in lineages
            if isinstance(lineage, dict)
        ]
    )

    def count_flag(name: str) -> int:
        return sum(1 for row in flags if row.get(name) is True)

    return {
        "schema_version": "accepted-source-lineage-v1",
        "coverage_status": "complete",
        "authoritative": True,
        "candidate_count": len(rows),
        "observed_candidate_count": len(rows),
        "expected_candidate_count": len(rows),
        "with_base_receipt_count": count_flag("has_base_receipt"),
        "with_cross_receipt_item_count": count_flag("has_cross_receipt_item"),
        "with_category_evidence_count": count_flag("has_category_evidence"),
        "with_nearest_real_structure_count": count_flag("has_nearest_real_structure"),
        "with_layout_integrity_count": count_flag("has_layout_integrity"),
        "with_arithmetic_reconciliation_count": count_flag(
            "has_arithmetic_reconciliation"
        ),
        "with_selection_evidence_count": count_flag("has_selection_evidence"),
        "source_receipt_key_count": len(source_keys),
        "source_receipt_keys": source_keys[:20],
        "source_receipt_keys_truncated": len(source_keys) > 20,
    }


def summarize_bundle_candidate_mix(
    rows: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize candidate coverage before and after LayoutLM selection."""
    rejected_rows = rejected_rows or []
    selected_by_key = {
        str(row.get("candidate_id") or row.get("receipt_key") or id(row))
        for row in selected_rows
    }
    merchants = sorted(
        {_candidate_merchant(row) for row in rows + selected_rows}
        | {_rejection_merchant(row) for row in rejected_rows}
    )
    merchant_rows: list[dict[str, Any]] = []
    for merchant in merchants:
        merchant_candidates = [
            row for row in rows if _candidate_merchant(row) == merchant
        ]
        merchant_selected = [
            row for row in selected_rows if _candidate_merchant(row) == merchant
        ]
        merchant_rejected = [
            row for row in rejected_rows if _rejection_merchant(row) == merchant
        ]
        scores = [
            score
            for row in merchant_selected
            if (score := _candidate_structure_score(row)) is not None
        ]
        quality_scores = [
            score
            for row in merchant_selected
            if (score := _candidate_quality_score(row)) is not None
        ]
        merchant_row = {
            "merchant_name": merchant,
            "candidate_count": len(merchant_candidates),
            "accepted_count": len(merchant_selected),
            "rejected_count": len(merchant_rejected),
            "rejection_reasons": _count_by(
                [_rejection_reason(row) for row in merchant_rejected]
            ),
            "operation_counts": _count_by(
                [_candidate_operation(row) for row in merchant_candidates]
            ),
            "accepted_operation_counts": _count_by(
                [_candidate_operation(row) for row in merchant_selected]
            ),
            "category_counts": _count_by(
                [
                    _candidate_category(row)
                    for row in merchant_candidates
                    if _candidate_category(row) != "unknown"
                ]
            ),
            "accepted_category_counts": _count_by(
                [
                    _candidate_category(row)
                    for row in merchant_selected
                    if _candidate_category(row) != "unknown"
                ]
            ),
            "field_replacement_counts": _count_by(
                [
                    label
                    for row in merchant_candidates
                    if (label := _candidate_field_replacement_label(row))
                ]
            ),
            "accepted_field_replacement_counts": _count_by(
                [
                    label
                    for row in merchant_selected
                    if (label := _candidate_field_replacement_label(row))
                ]
            ),
            "grounded_candidate_count": sum(
                1 for row in merchant_candidates if _candidate_is_grounded(row)
            ),
            "accepted_grounded_candidate_count": sum(
                1 for row in merchant_selected if _candidate_is_grounded(row)
            ),
            "arithmetic_candidate_count": sum(
                1 for row in merchant_candidates if _candidate_has_arithmetic(row)
            ),
            "accepted_arithmetic_candidate_count": sum(
                1 for row in merchant_selected if _candidate_has_arithmetic(row)
            ),
            "accepted_structure_similarity": _score_summary(scores),
            "accepted_structure_components": _component_score_summary(
                merchant_selected
            ),
            "accepted_real_baseline_comparison": (
                _real_baseline_comparison_summary(merchant_selected)
            ),
            "accepted_source_lineage": _accepted_source_lineage_summary(
                merchant_selected
            ),
            "accepted_candidate_ids": [
                str(row.get("candidate_id") or row.get("receipt_key") or "")
                for row in merchant_selected[:10]
            ],
        }
        if quality_scores:
            merchant_row["accepted_candidate_quality"] = _score_summary(quality_scores)
            merchant_row["accepted_candidate_quality_components"] = (
                _candidate_quality_component_summary(merchant_selected)
            )
        merchant_rows.append(merchant_row)

    all_scores = [
        score
        for row in selected_rows
        if (score := _candidate_structure_score(row)) is not None
    ]
    all_quality_scores = [
        score
        for row in selected_rows
        if (score := _candidate_quality_score(row)) is not None
    ]
    result = {
        "candidate_count": len(rows),
        "accepted_count": len(selected_rows),
        "rejected_count": len(rejected_rows),
        "rejection_reasons": _count_by(
            [_rejection_reason(row) for row in rejected_rows]
        ),
        "operation_counts": _count_by([_candidate_operation(row) for row in rows]),
        "accepted_operation_counts": _count_by(
            [_candidate_operation(row) for row in selected_rows]
        ),
        "category_counts": _count_by(
            [
                _candidate_category(row)
                for row in rows
                if _candidate_category(row) != "unknown"
            ]
        ),
        "accepted_category_counts": _count_by(
            [
                _candidate_category(row)
                for row in selected_rows
                if _candidate_category(row) != "unknown"
            ]
        ),
        "field_replacement_counts": _count_by(
            [
                label
                for row in rows
                if (label := _candidate_field_replacement_label(row))
            ]
        ),
        "accepted_field_replacement_counts": _count_by(
            [
                label
                for row in selected_rows
                if (label := _candidate_field_replacement_label(row))
            ]
        ),
        "accepted_structure_similarity": _score_summary(all_scores),
        "accepted_structure_components": _component_score_summary(selected_rows),
        "accepted_real_baseline_comparison": _real_baseline_comparison_summary(
            selected_rows
        ),
        "accepted_source_lineage": _accepted_source_lineage_summary(selected_rows),
        "accepted_grounded_candidate_count": sum(
            1 for row in selected_rows if _candidate_is_grounded(row)
        ),
        "accepted_arithmetic_candidate_count": sum(
            1 for row in selected_rows if _candidate_has_arithmetic(row)
        ),
        "accepted_candidate_keys": sorted(selected_by_key)[:50],
        "merchant_count": len(merchant_rows),
        "accepted_merchant_count": sum(
            1 for row in merchant_rows if row["accepted_count"]
        ),
        "accepted_mix_balance": _accepted_mix_balance(selected_rows),
        "merchants": merchant_rows[:50],
    }
    if all_quality_scores:
        result["accepted_candidate_quality"] = _score_summary(all_quality_scores)
        result["accepted_candidate_quality_components"] = (
            _candidate_quality_component_summary(selected_rows)
        )
    return result


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if not numerator or not denominator:
        return 0.0 if denominator else None
    return round(numerator / denominator, 3)


def _clip_text(value: Any, *, max_chars: int = 120) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text if len(text) <= max_chars else f"{text[: max_chars - 1]}..."


def _contract_ready_operations(contract: dict[str, Any]) -> list[str]:
    operations = contract.get("operation_contracts") or {}
    if not isinstance(operations, dict):
        return []
    return [
        str(name)
        for name, operation_contract in sorted(operations.items())
        if isinstance(operation_contract, dict)
        and operation_contract.get("ready") is True
    ]


def _contract_mutable_fields(contract: dict[str, Any]) -> list[str]:
    replace_contract = (contract.get("operation_contracts") or {}).get(
        "replace_field"
    ) or {}
    fields = replace_contract.get("fields") or {}
    if not isinstance(fields, dict):
        return []
    return [
        str(label)
        for label, field in sorted(fields.items())
        if isinstance(field, dict)
        and field.get("safe_to_mutate") is True
        and field.get("stable_geometry") is True
        and field.get("stable_format")
    ]


def _report_source_quality_by_merchant(
    artifacts: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for artifact in artifacts or []:
        if not isinstance(artifact, dict):
            continue
        source_quality = _artifact_source_quality(artifact)
        if not source_quality:
            continue
        merchant_name = str(
            source_quality.get("merchant_name")
            or _artifact_merchant(artifact)
            or "Unknown merchant"
        )
        rows[merchant_name] = source_quality
    return rows


def _report_source_quality_fields(
    source_quality: dict[str, Any],
) -> dict[str, Any]:
    if not source_quality:
        return {}
    return {
        key: value
        for key, value in {
            "source_quality_status": source_quality.get("status"),
            "source_quality_receipt_count": _safe_int(
                source_quality.get("receipt_count")
            ),
            "source_quality_labeled_word_count": _safe_int(
                source_quality.get("labeled_word_count")
            ),
            "source_quality_receipts_with_line_item_labels": _safe_int(
                source_quality.get("receipts_with_line_item_labels")
            ),
            "source_quality_receipts_with_grand_total_label": _safe_int(
                source_quality.get("receipts_with_grand_total_label")
            ),
            "source_quality_receipts_with_date_or_time_label": _safe_int(
                source_quality.get("receipts_with_date_or_time_label")
            ),
            "source_quality_text_structure_status": source_quality.get(
                "text_structure_status"
            ),
            "source_quality_line_item_like_text_line_count": _safe_int(
                source_quality.get("line_item_like_text_line_count")
            ),
            "source_quality_total_like_text_line_count": _safe_int(
                source_quality.get("total_like_text_line_count")
            ),
            "source_quality_limitations": _source_quality_limitations(source_quality),
            "source_quality_requires_label_validation": (
                True
                if _source_quality_has_recoverable_unlabeled_text(source_quality)
                else None
            ),
            "source_quality_operation_blockers": _source_quality_operation_blockers(
                source_quality
            ),
        }.items()
        if value not in (None, "", [], {})
    }


def _candidate_accuracy_checks(candidate: dict[str, Any]) -> list[str]:
    evidence = _candidate_metadata(candidate).get("synthesis_accuracy_evidence")
    if not isinstance(evidence, dict):
        return []
    return [str(check) for check in (evidence.get("checks") or [])[:8] if check]


def _preview_focus_lines(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    preview = _candidate_metadata(candidate).get("synthetic_receipt_preview")
    if not isinstance(preview, dict):
        return []
    lines = [
        line
        for line in preview.get("lines") or []
        if isinstance(line, dict) and line.get("text")
    ]
    focused = [
        line
        for line in lines
        if line.get("synthetic_insert") or line.get("modified_labels")
    ]
    if not focused:
        evidence = _candidate_metadata(candidate).get("synthesis_accuracy_evidence")
        changed_text = (
            evidence.get("changed_text") if isinstance(evidence, dict) else None
        )
        replacement = _candidate_metadata(candidate).get("field_replacement")
        replacement_text = (
            replacement.get("new_text") if isinstance(replacement, dict) else None
        )
        needles = [str(value) for value in (changed_text, replacement_text) if value]
        focused = [
            line
            for line in lines
            if any(needle in str(line.get("text") or "") for needle in needles)
        ]
    if not focused:
        focused = lines[:2]
    return [
        {
            "line_number": _safe_int(line.get("line_number")),
            "text": _clip_text(line.get("text")),
            "role": line.get("role"),
            "y": _safe_float(line.get("y")),
            "bbox": line.get("bbox"),
            "synthetic_insert": line.get("synthetic_insert") is True,
            "modified_labels": list(line.get("modified_labels") or [])[:5],
        }
        for line in focused[:3]
    ]


def _compact_report_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    preview = _candidate_metadata(candidate).get("synthetic_receipt_preview")
    evidence = _candidate_metadata(candidate).get("synthesis_accuracy_evidence")
    accuracy = evidence if isinstance(evidence, dict) else {}
    result = {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "receipt_key": str(candidate.get("receipt_key") or ""),
        "operation": _candidate_operation(candidate),
        "category": (
            _candidate_category(candidate)
            if _candidate_category(candidate) != "unknown"
            else None
        ),
        "structure_similarity": _candidate_structure_score(candidate),
        "accuracy_checks": _candidate_accuracy_checks(candidate),
    }
    quality = _candidate_quality(candidate)
    if quality:
        result["candidate_quality"] = quality
    selection_evidence = _candidate_selection_evidence(candidate)
    if selection_evidence:
        result["selection_evidence"] = selection_evidence
    source_lineage = _candidate_source_lineage(candidate)
    if source_lineage:
        result["source_lineage"] = source_lineage
    if accuracy.get("changed_text"):
        result["changed_text"] = _clip_text(accuracy.get("changed_text"))
    if accuracy.get("label"):
        result["label"] = accuracy.get("label")
    if accuracy.get("old_text") or accuracy.get("new_text"):
        result["field_replacement"] = {
            "old_text": accuracy.get("old_text"),
            "new_text": accuracy.get("new_text"),
            "format": accuracy.get("format"),
        }
    if accuracy.get("old_grand_total") or accuracy.get("new_grand_total"):
        result["total_change"] = {
            "old_grand_total": accuracy.get("old_grand_total"),
            "new_grand_total": accuracy.get("new_grand_total"),
            "tax_delta": accuracy.get("tax_delta"),
        }
    if isinstance(accuracy.get("catalog_grounding"), dict):
        result["catalog_grounding"] = accuracy["catalog_grounding"]
    category_placement = _compact_category_placement(
        accuracy.get("category_placement")
    )
    if category_placement:
        result["category_placement"] = category_placement
    removal_context = _compact_removal_context(accuracy.get("removal_context"))
    if removal_context:
        result["removal_context"] = removal_context
    if isinstance(preview, dict):
        result["receipt_shape"] = {
            "line_count": _safe_int(preview.get("line_count")),
            "token_count": _safe_int(preview.get("token_count")),
            "truncated": preview.get("truncated") is True,
            "coordinate_system": preview.get("coordinate_system"),
        }
        focus_lines = _preview_focus_lines(candidate)
        if focus_lines:
            result["preview_lines"] = focus_lines
    return result


def _compact_removal_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "category": value.get("category"),
        "removed_y": _safe_float(value.get("removed_y")),
        "line_step": _safe_int(value.get("line_step")),
        "shifted_lower_lines_by": _safe_int(value.get("shifted_lower_lines_by")),
        "shifted_line_count": _safe_int(value.get("shifted_line_count")),
        "shifted_lower_line_shift_min": _safe_int(
            value.get("shifted_lower_line_shift_min")
        ),
        "shifted_lower_line_shift_max": _safe_int(
            value.get("shifted_lower_line_shift_max")
        ),
        "category_item_count_before": _safe_int(
            value.get("category_item_count_before")
        ),
        "category_item_count_after": _safe_int(
            value.get("category_item_count_after")
        ),
        "selection_reason": _clip_text(
            str(value.get("selection_reason") or ""), max_chars=180
        ),
    }
    return {
        key: item for key, item in result.items() if item not in (None, "", [], {})
    }


def _compact_category_placement(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "category": value.get("category"),
        "insert_y": _safe_float(value.get("insert_y")),
        "line_step": _safe_int(value.get("line_step")),
        "shifted_lower_lines_by": _safe_int(value.get("shifted_lower_lines_by")),
        "shifted_line_count": _safe_int(value.get("shifted_line_count")),
        "shifted_lower_line_shift_min": _safe_int(
            value.get("shifted_lower_line_shift_min")
        ),
        "shifted_lower_line_shift_max": _safe_int(
            value.get("shifted_lower_line_shift_max")
        ),
        "category_item_count_before": _safe_int(
            value.get("category_item_count_before")
        ),
        "nearest_category_item_y": _safe_float(value.get("nearest_category_item_y")),
        "nearest_lower_line_y": _safe_float(value.get("nearest_lower_line_y")),
        "same_category_section": (
            value.get("same_category_section")
            if isinstance(value.get("same_category_section"), bool)
            else None
        ),
        "selection_reason": _clip_text(
            str(value.get("selection_reason") or ""), max_chars=180
        ),
        "base_receipt_has_category": (
            value.get("base_receipt_has_category")
            if isinstance(value.get("base_receipt_has_category"), bool)
            else None
        ),
        "category_seen_count": _safe_int(value.get("category_seen_count")),
        "category_heading_seen_count": _safe_int(
            value.get("category_heading_seen_count")
        ),
        "category_alignment": value.get("category_alignment"),
    }
    return {
        key: item for key, item in result.items() if item not in (None, "", [], {})
    }


def _selection_reason(candidate: dict[str, Any]) -> str:
    if _candidate_quality_score(candidate) is not None:
        return "declared_candidate_quality_ranked_after_layoutlm_gates"

    operation = _candidate_operation(candidate)
    if operation == "add_line_item":
        return "grounded_add_item_with_arithmetic_and_structure_similarity"
    if operation == "remove_line_item":
        return "non_taxable_remove_item_with_arithmetic_and_structure_similarity"
    if operation == "replace_field":
        return "stable_field_replacement_after_layoutlm_gates"
    if operation == "hard_negative":
        return "hard_negative_false_positive_geometry_after_layoutlm_gates"
    return "layoutlm_quality_gates_passed"


def _compact_selection_candidate(
    candidate: dict[str, Any],
    *,
    rank: int,
) -> dict[str, Any]:
    result = _compact_report_candidate(candidate)
    result["rank"] = rank
    result["merchant_name"] = _candidate_merchant(candidate)
    if candidate.get("image_id"):
        result["image_id"] = str(candidate.get("image_id"))
    result["selection_reason"] = _selection_reason(candidate)
    return result


def _report_recommendations(
    bundle: dict[str, Any],
    *,
    candidate_mix: dict[str, Any],
    contracts: list[dict[str, Any]],
    accepted_operation_coverage: dict[str, Any] | None = None,
) -> list[str]:
    recommendations: list[str] = []
    if bundle.get("ready") is not True:
        recommendations.append("resolve_bundle_reasons_before_training")
    if not _safe_int(candidate_mix.get("accepted_count")):
        recommendations.append("collect_more_receipts_or_fix_parameterization")
    if any(str(contract.get("status") or "") != "ready" for contract in contracts):
        recommendations.append("collect_more_receipts_for_not_ready_merchants")
    if any(
        "source_receipt_quality_blocked" in set(contract.get("blockers") or [])
        or str((contract.get("source_receipt_quality") or {}).get("status") or "")
        == "blocked"
        for contract in contracts
    ):
        recommendations.append("fix_source_receipt_quality_before_synthesis")
    if any(
        _source_quality_has_recoverable_unlabeled_text(
            contract.get("source_receipt_quality") or {}
        )
        for contract in contracts
    ):
        recommendations.append("validate_recoverable_unlabeled_receipts")
    rejection_reasons = candidate_mix.get("rejection_reasons") or {}
    if rejection_reasons.get("operation_not_supported_by_contract"):
        recommendations.append("enable_only_contract_supported_mutations")
    if rejection_reasons.get("low_structure_similarity"):
        recommendations.append("inspect_low_similarity_candidates_before_training")
    if _safe_int(candidate_mix.get("accepted_arithmetic_candidate_count")):
        recommendations.append("verify_total_and_tax_reconciliation_in_preview")
    if _safe_int(candidate_mix.get("accepted_grounded_candidate_count")):
        recommendations.append("prefer_cross_receipt_grounded_item_mutations")
    if accepted_operation_coverage and accepted_operation_coverage.get(
        "uncovered_ready_operations"
    ):
        recommendations.append("cover_ready_operations_before_training")
    balance = candidate_mix.get("accepted_mix_balance") or {}
    if str(balance.get("risk_level") or "").lower() in {"medium", "high"}:
        recommendations.append("rebalance_synthetic_mix_before_training")
    if not recommendations:
        recommendations.append("bundle_ready_for_train_only_layoutlm_augmentation")
    return recommendations[:8]


def _training_ready_reasons(
    bundle: dict[str, Any],
    *,
    candidate_mix: dict[str, Any],
    contracts: list[dict[str, Any]],
    accepted_operation_coverage: dict[str, Any] | None = None,
    llm_model_freshness_gate: dict[str, Any] | None = None,
) -> list[str]:
    """Return blocking reasons before synthetic rows should train LayoutLM."""
    reasons: list[str] = []
    accepted_count = _safe_int(candidate_mix.get("accepted_count")) or 0
    accepted_source_lineage = candidate_mix.get("accepted_source_lineage") or {}
    if bundle.get("ready") is not True:
        reasons.append("resolve_bundle_reasons_before_training")
    if not accepted_count:
        reasons.append("collect_more_receipts_or_fix_parameterization")
    if any(str(contract.get("status") or "") != "ready" for contract in contracts):
        reasons.append("collect_more_receipts_for_not_ready_merchants")
    if any(
        "source_receipt_quality_blocked" in set(contract.get("blockers") or [])
        or str((contract.get("source_receipt_quality") or {}).get("status") or "")
        == "blocked"
        for contract in contracts
    ):
        reasons.append("fix_source_receipt_quality_before_synthesis")
    if any(
        _source_quality_has_recoverable_unlabeled_text(
            contract.get("source_receipt_quality") or {}
        )
        for contract in contracts
    ):
        reasons.append("validate_recoverable_unlabeled_receipts")
    if accepted_operation_coverage and accepted_operation_coverage.get(
        "uncovered_ready_operations"
    ):
        reasons.append("cover_ready_operations_before_training")
    if (
        llm_model_freshness_gate
        and llm_model_freshness_gate.get("requires_current_model_guidance") is True
        and llm_model_freshness_gate.get("passed") is not True
    ):
        reasons.append("refresh_latest_model_guidance_before_synthesis")
    if _accepted_source_lineage_incomplete(
        accepted_source_lineage,
        accepted_count=accepted_count,
    ):
        reasons.append("complete_source_lineage_before_training")
    balance = candidate_mix.get("accepted_mix_balance") or {}
    if str(balance.get("risk_level") or "").lower() in {"medium", "high"}:
        reasons.append("rebalance_synthetic_mix_before_training")
    return reasons[:8]


def _accepted_source_lineage_incomplete(
    accepted_source_lineage: dict[str, Any],
    *,
    accepted_count: int,
) -> bool:
    """Fail closed when accepted rows cannot all prove base receipt lineage."""
    if accepted_count <= 0:
        return False
    if not isinstance(accepted_source_lineage, dict) or not accepted_source_lineage:
        return True
    if accepted_source_lineage.get("authoritative") is False:
        return True
    # Candidate/observed counts can be bounded diagnostics; base coverage is
    # the full-population training invariant enforced here.
    with_base_count = _safe_int(accepted_source_lineage.get("with_base_receipt_count"))
    if with_base_count is None or with_base_count < accepted_count:
        return True
    return False


def _synthetic_mix_balance_failure(
    *,
    preflight: dict[str, Any],
    candidate_mix: dict[str, Any],
) -> str | None:
    """Return a bundle-level failure when selected rows are merchant-skewed."""
    ready_merchants = _safe_int(preflight.get("ready_merchant_count")) or 0
    if ready_merchants <= 1:
        return None

    balance = candidate_mix.get("accepted_mix_balance") or {}
    if str(balance.get("risk_level") or "").strip().lower() != "high":
        return None
    risk_reasons = {str(reason) for reason in balance.get("risk_reasons") or []}
    if "single_merchant_accepted" in risk_reasons:
        return "accepted_synthetic_mix_single_merchant_high_risk"
    if "top_merchant_share_ge_80pct" in risk_reasons:
        return "accepted_synthetic_mix_top_merchant_high_risk"
    return "accepted_synthetic_mix_high_risk"


def build_local_synthesis_quality_report(
    bundle: dict[str, Any],
    *,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a bounded local report explaining synthetic training readiness."""
    candidate_mix = bundle.get("candidate_mix") or {}
    contracts = [
        row
        for row in bundle.get("merchant_synthesis_contracts") or []
        if isinstance(row, dict)
    ]
    contracts_by_merchant = {
        str(contract.get("merchant_name") or "Unknown merchant"): contract
        for contract in contracts
    }
    source_quality_by_merchant = _report_source_quality_by_merchant(artifacts)
    mix_by_merchant = _merchant_mix_lookup(candidate_mix)
    artifact_audit_by_merchant = {
        str(row.get("merchant_name") or "Unknown merchant"): row
        for row in summarize_merchant_synthesis_audit(artifacts or [])
    }
    accepted_rows = [
        row
        for row in bundle.get("synthetic_training_examples") or []
        if isinstance(row, dict)
    ]
    accepted_by_merchant: dict[str, list[dict[str, Any]]] = {}
    for row in accepted_rows:
        accepted_by_merchant.setdefault(_candidate_merchant(row), []).append(row)

    rejected_by_merchant: dict[str, list[dict[str, Any]]] = {}
    for row in (bundle.get("selection") or {}).get("rejected_candidate_examples") or []:
        if isinstance(row, dict):
            rejected_by_merchant.setdefault(_rejection_merchant(row), []).append(row)

    merchant_names = sorted(
        set(contracts_by_merchant)
        | set(mix_by_merchant)
        | set(artifact_audit_by_merchant)
        | set(accepted_by_merchant)
        | set(rejected_by_merchant)
    )
    merchant_rows: list[dict[str, Any]] = []
    for merchant in merchant_names:
        contract = contracts_by_merchant.get(merchant, {})
        mix = mix_by_merchant.get(merchant, {})
        audit = artifact_audit_by_merchant.get(merchant, {})
        source_quality = source_quality_by_merchant.get(merchant) or (
            contract.get("source_receipt_quality") or {}
        )
        accepted_count = _safe_int(mix.get("accepted_count")) or 0
        candidate_count = _safe_int(mix.get("candidate_count")) or 0
        merchant_row = {
            "merchant_name": merchant,
            "readiness_status": contract.get("status")
            or audit.get("readiness_status")
            or "missing",
            "readiness_score": _safe_float(
                contract.get("score") or audit.get("readiness_score")
            ),
            "source_receipt_count": _safe_int(
                contract.get("source_receipt_count")
                or audit.get("source_receipt_count")
            )
            or 0,
            "candidate_count": candidate_count,
            "accepted_count": accepted_count,
            "rejected_count": _safe_int(mix.get("rejected_count")) or 0,
            "acceptance_rate": _ratio(accepted_count, candidate_count),
            "supported_operations": list(
                contract.get("supported_operations")
                or audit.get("mutation_inventory", {}).get(
                    "supported_operations",
                    [],
                )
            )[:8],
            "contract_ready_operations": _contract_ready_operations(contract),
            "accepted_operation_counts": mix.get("accepted_operation_counts") or {},
            "accepted_category_counts": mix.get("accepted_category_counts") or {},
            "accepted_field_replacement_counts": mix.get(
                "accepted_field_replacement_counts"
            )
            or {},
            "safe_mutable_fields": _contract_mutable_fields(contract),
            "operation_readiness": audit.get("operation_readiness") or [],
            "missing_operations": list(audit.get("missing_operations") or [])[:8],
            "next_synthesis_actions": list(audit.get("next_synthesis_actions") or [])[
                :8
            ],
            "accepted_structure_similarity": mix.get("accepted_structure_similarity")
            or {},
            "accepted_structure_components": mix.get("accepted_structure_components")
            or {},
            "accepted_real_baseline_comparison": mix.get(
                "accepted_real_baseline_comparison"
            )
            or {},
            "accepted_candidate_quality": mix.get("accepted_candidate_quality") or {},
            "accepted_candidate_quality_components": mix.get(
                "accepted_candidate_quality_components"
            )
            or {},
            "accepted_source_lineage": mix.get("accepted_source_lineage") or {},
            "rejection_reasons": mix.get("rejection_reasons") or {},
            "blockers": list(contract.get("blockers") or audit.get("blockers") or [])[
                :8
            ],
            "limitations": list(
                contract.get("limitations") or audit.get("limitations") or []
            )[:8],
            "accepted_examples": [
                _compact_report_candidate(row)
                for row in accepted_by_merchant.get(merchant, [])[:3]
            ],
            "rejected_examples": rejected_by_merchant.get(merchant, [])[:5],
        }
        merchant_row.update(_report_source_quality_fields(source_quality))
        merchant_rows.append(merchant_row)

    operation_coverage = _operation_coverage_from_rows(merchant_rows)
    accepted_operation_coverage = _accepted_operation_coverage_from_rows(merchant_rows)
    merchant_gap_summary = _merchant_gap_summary(merchant_rows)
    accepted_count = _safe_int(candidate_mix.get("accepted_count")) or 0
    candidate_count = _safe_int(candidate_mix.get("candidate_count")) or 0
    llm_execution = (bundle.get("preflight") or {}).get("llm_execution") or {}
    llm_model_freshness_gate = _llm_model_freshness_gate(llm_execution)
    training_ready_reasons = _training_ready_reasons(
        bundle,
        candidate_mix=candidate_mix,
        contracts=contracts,
        accepted_operation_coverage=accepted_operation_coverage,
        llm_model_freshness_gate=llm_model_freshness_gate,
    )
    training_batch_policy = (
        bundle.get("synthetic_training_batch_policy")
        or (bundle.get("selection") or {}).get("training_batch_policy")
        or {}
    )
    source_quality_status_counts = _count_by(
        [
            str(row.get("source_quality_status"))
            for row in merchant_rows
            if row.get("source_quality_status")
        ]
    )
    report = {
        "schema_version": "local-synthesis-quality-report-v1",
        "ready": bundle.get("ready") is True and accepted_count > 0,
        "training_ready": not training_ready_reasons,
        "training_ready_reasons": training_ready_reasons,
        "bundle_ready": bundle.get("ready") is True,
        "bundle_reasons": list(bundle.get("reasons") or [])[:10],
        "summary": {
            "merchant_count": _safe_int(candidate_mix.get("merchant_count"))
            or len(merchant_rows),
            "accepted_merchant_count": _safe_int(
                candidate_mix.get("accepted_merchant_count")
            )
            or 0,
            "candidate_count": candidate_count,
            "accepted_count": accepted_count,
            "rejected_count": _safe_int(candidate_mix.get("rejected_count")) or 0,
            "acceptance_rate": _ratio(accepted_count, candidate_count),
            "accepted_operation_counts": candidate_mix.get("accepted_operation_counts")
            or {},
            "accepted_category_counts": candidate_mix.get("accepted_category_counts")
            or {},
            "accepted_field_replacement_counts": candidate_mix.get(
                "accepted_field_replacement_counts"
            )
            or {},
            "accepted_structure_similarity": candidate_mix.get(
                "accepted_structure_similarity"
            )
            or {},
            "accepted_structure_components": candidate_mix.get(
                "accepted_structure_components"
            )
            or {},
            "accepted_real_baseline_comparison": candidate_mix.get(
                "accepted_real_baseline_comparison"
            )
            or {},
            "accepted_candidate_quality": candidate_mix.get(
                "accepted_candidate_quality"
            )
            or {},
            "accepted_candidate_quality_components": candidate_mix.get(
                "accepted_candidate_quality_components"
            )
            or {},
            "accepted_source_lineage": candidate_mix.get("accepted_source_lineage")
            or {},
            "accepted_mix_balance": candidate_mix.get("accepted_mix_balance") or {},
            "llm_execution": llm_execution,
            "rejection_reasons": candidate_mix.get("rejection_reasons") or {},
            "next_synthesis_action_counts": _next_synthesis_action_counts(
                merchant_rows
            ),
            "contract_count": len(contracts),
            "ready_contract_count": sum(
                1
                for contract in contracts
                if str(contract.get("status") or "").strip().lower() == "ready"
            ),
            "source_quality_status_counts": source_quality_status_counts,
            "blocked_source_quality_merchant_count": (
                source_quality_status_counts.get("blocked", 0)
            ),
        },
        "operation_coverage": operation_coverage,
        "accepted_operation_coverage": accepted_operation_coverage,
        "merchant_gap_summary": merchant_gap_summary,
        "training_batch_policy": training_batch_policy,
        "quality_gates": {
            "validation_policy": bundle.get("validation_policy")
            or "real_receipts_only",
            "train_only_examples": True,
            "contract_gate": (bundle.get("selection") or {}).get("contract_gate") or {},
            "max_per_merchant": (bundle.get("selection") or {}).get("max_per_merchant"),
            "max_per_merchant_operation": (bundle.get("selection") or {}).get(
                "max_per_merchant_operation"
            ),
            "min_structure_similarity": (bundle.get("selection") or {}).get(
                "min_structure_similarity"
            ),
            "structure_component_thresholds": (
                (bundle.get("selection") or {}).get("structure_component_thresholds")
                or {}
            ),
            "accepted_operation_coverage_gate": {
                "enabled": True,
                "passed": not accepted_operation_coverage.get(
                    "uncovered_ready_operations"
                ),
                "ready_operation_count": accepted_operation_coverage.get(
                    "ready_operation_count"
                ),
                "accepted_ready_operation_count": accepted_operation_coverage.get(
                    "accepted_ready_operation_count"
                ),
                "uncovered_ready_operations": list(
                    accepted_operation_coverage.get("uncovered_ready_operations") or []
                )[:8],
            },
            "llm_model_freshness_gate": llm_model_freshness_gate,
        },
        "recommendations": _report_recommendations(
            bundle,
            candidate_mix=candidate_mix,
            contracts=contracts,
            accepted_operation_coverage=accepted_operation_coverage,
        ),
        "merchants": merchant_rows[:50],
    }
    return report


def _compact_readiness_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    profile = artifact.get("merchant_receipt_parameterization") or {}
    readiness = profile.get("synthesis_readiness") or {}
    candidates = artifact.get("synthetic_receipt_candidates") or []
    source_quality = _artifact_source_quality(artifact)
    source_quality_operation_blockers = _source_quality_operation_blockers(
        source_quality
    )
    candidate_operation_counts = _count_by(
        [
            _candidate_operation(candidate)
            for candidate in candidates
            if isinstance(candidate, dict)
        ]
    )
    grounded = [
        candidate for candidate in candidates if _candidate_is_grounded(candidate)
    ]
    arithmetic = [
        candidate
        for candidate in candidates
        if (candidate.get("metadata") or {}).get("arithmetic_reconciliation")
    ]
    accepted_operation_counts = _accepted_operation_counts_for_row(artifact)
    accepted_count = _safe_int(artifact.get("accepted_count"))
    if accepted_count is None:
        accepted_count = sum(accepted_operation_counts.values())
    readiness_status = _source_quality_effective_status(
        str(readiness.get("status") or "missing"),
        source_quality,
    )
    return {
        "merchant_name": readiness.get("merchant_name") or _artifact_merchant(artifact),
        "readiness_status": readiness_status,
        "readiness_score": _safe_float(readiness.get("score")),
        "source_quality_status": source_quality.get("status"),
        "source_quality_receipt_count": _safe_int(source_quality.get("receipt_count")),
        "source_quality_labeled_word_count": _safe_int(
            source_quality.get("labeled_word_count")
        ),
        "source_quality_operation_blockers": source_quality_operation_blockers,
        "supported_operations": list(readiness.get("supported_operations") or [])[:5],
        "candidate_operation_counts": candidate_operation_counts,
        "candidate_capacity": _safe_int(readiness.get("candidate_capacity")),
        "candidate_count": len(candidates),
        "accepted_count": accepted_count,
        "accepted_operation_counts": accepted_operation_counts,
        "grounded_candidate_count": len(grounded),
        "arithmetic_candidate_count": len(arithmetic),
        "hard_negative_label_count": _safe_int(
            readiness.get("hard_negative_label_count")
        )
        or 0,
        "grounded_add_item_candidate_count": _safe_int(
            readiness.get("grounded_add_item_candidate_count")
        )
        or 0,
        "removable_item_candidate_count": _safe_int(
            readiness.get("removable_item_candidate_count")
        )
        or 0,
        "mutable_field_count": _safe_int(readiness.get("mutable_field_count")) or 0,
        "llm_execution": _artifact_llm_execution(artifact),
        "blockers": list(
            dict.fromkeys(
                list(readiness.get("blockers") or [])
                + _source_quality_blockers(source_quality)
            )
        )[:8],
        "limitations": list(
            dict.fromkeys(
                list(readiness.get("limitations") or [])
                + _source_quality_limitations(source_quality)
            )
        )[:8],
    }


def _source_quality_row_operation_blocker(
    row: dict[str, Any],
    operation: str,
) -> str | None:
    blockers = row.get("source_quality_operation_blockers")
    if isinstance(blockers, dict):
        value = blockers.get(operation)
        if value:
            return str(value)
    return None


def _operation_gap_reasons(row: dict[str, Any], operation: str) -> list[str]:
    status = str(row.get("readiness_status") or row.get("status") or "").lower()
    supported = operation in set(row.get("supported_operations") or [])
    has_candidate = (_operation_counts_for_row(row).get(operation) or 0) > 0
    source_quality_blocker = _source_quality_row_operation_blocker(row, operation)
    if (
        status in {"ready", "partial"}
        and (supported or has_candidate)
        and not source_quality_blocker
    ):
        return []
    blockers = [str(item) for item in row.get("blockers") or [] if item]
    limitations = [str(item) for item in row.get("limitations") or [] if item]
    reasons: list[str] = []
    if source_quality_blocker:
        reasons.append(source_quality_blocker)
    if status and status not in {"ready", "partial"}:
        reasons.append(f"readiness_status_{status}")
    relevant = {
        "hard_negative": {
            "no_supported_operations",
            "plan_has_no_supported_hard_negative_slots",
            "no_receipts",
        },
        "add_line_item": {
            "no_line_items",
            "no_observed_item_catalog",
            "no_cross_receipt_grounded_add_items",
            "no_grand_total_anchors",
            "no_category_structure",
            "no_supported_operations",
        },
        "remove_line_item": {
            "no_line_items",
            "no_removable_non_taxable_items",
            "no_grand_total_anchors",
            "no_supported_operations",
        },
        "replace_field": {
            "no_receipts",
            "no_supported_operations",
        },
    }.get(operation, set())
    for reason in blockers + limitations:
        if reason in relevant and reason not in reasons:
            reasons.append(reason)
    if operation == "hard_negative" and not row.get("hard_negative_label_count"):
        reasons.append("no_supported_hard_negative_slots")
    if operation == "add_line_item" and not row.get(
        "grounded_add_item_candidate_count"
    ):
        reasons.append("no_cross_receipt_grounded_add_items")
    if operation == "remove_line_item" and not row.get(
        "removable_item_candidate_count"
    ):
        reasons.append("no_removable_non_taxable_items")
    if operation == "replace_field" and not row.get("mutable_field_count"):
        reasons.append("no_safe_mutable_fields")
    return list(dict.fromkeys(reasons))[:5]


def _operation_counts_for_row(row: dict[str, Any]) -> dict[str, int]:
    for key in (
        "candidate_operation_counts",
        "accepted_operation_counts",
        "operation_counts",
    ):
        counts = row.get(key)
        if isinstance(counts, dict):
            return {
                str(name): count
                for name, value in counts.items()
                if (count := _safe_int(value)) is not None and count > 0
            }
    return {}


def _accepted_operation_counts_for_row(row: dict[str, Any]) -> dict[str, int]:
    counts = row.get("accepted_operation_counts")
    if not isinstance(counts, dict):
        return {}
    return {
        str(name): count
        for name, value in counts.items()
        if (count := _safe_int(value)) is not None and count > 0
    }


def _row_operation_ready(row: dict[str, Any], operation: str) -> bool:
    if _source_quality_row_operation_blocker(row, operation):
        return False
    status = str(row.get("readiness_status") or row.get("status") or "").lower()
    has_candidate = (_operation_counts_for_row(row).get(operation) or 0) > 0
    return status in {"ready", "partial"} and (
        operation in set(row.get("supported_operations") or []) or has_candidate
    )


def _accepted_operation_coverage_from_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    operations: dict[str, dict[str, Any]] = {}
    uncovered_operations: list[str] = []
    accepted_ready_operation_count = 0
    ready_operation_count = 0
    accepted_operation_count = 0

    for operation in SYNTHESIS_OPERATION_FAMILIES:
        ready_merchants = [
            str(row.get("merchant_name") or "Unknown merchant")
            for row in rows
            if _row_operation_ready(row, operation)
        ]
        accepted_counts_by_merchant = {
            str(row.get("merchant_name") or "Unknown merchant"): count
            for row in rows
            if (count := _accepted_operation_counts_for_row(row).get(operation))
        }
        accepted_merchants = sorted(accepted_counts_by_merchant)
        ready_accepted_merchants = [
            merchant
            for merchant in ready_merchants
            if merchant in accepted_counts_by_merchant
        ]
        uncovered_ready_merchants = [
            merchant
            for merchant in ready_merchants
            if merchant not in accepted_counts_by_merchant
        ]
        accepted_count = sum(accepted_counts_by_merchant.values())
        ready_count = len(ready_merchants)
        accepted_ready_count = len(ready_accepted_merchants)
        if ready_count:
            ready_operation_count += 1
        if accepted_count:
            accepted_operation_count += 1
        if ready_count and accepted_ready_count:
            accepted_ready_operation_count += 1
        if ready_count and not accepted_ready_count:
            uncovered_operations.append(operation)

        operations[operation] = {
            "ready_merchant_count": ready_count,
            "accepted_merchant_count": len(accepted_merchants),
            "accepted_ready_merchant_count": accepted_ready_count,
            "accepted_count": accepted_count,
            "ready_acceptance_share": _ratio(accepted_ready_count, ready_count),
            "ready_merchants": ready_merchants[:10],
            "accepted_merchants": accepted_merchants[:10],
            "uncovered_ready_merchants": uncovered_ready_merchants[:10],
        }

    recommendations = (
        ["cover_ready_operations_before_training"] if uncovered_operations else []
    )
    return {
        "operation_count": len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": ready_operation_count,
        "accepted_operation_count": accepted_operation_count,
        "accepted_ready_operation_count": accepted_ready_operation_count,
        "accepted_ready_operation_share": _ratio(
            accepted_ready_operation_count,
            ready_operation_count,
        ),
        "uncovered_ready_operations": uncovered_operations,
        "operations": operations,
        "recommendations": recommendations,
    }


def _operation_coverage_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    merchant_count = len(rows)
    operations: dict[str, dict[str, Any]] = {}
    recommendations: list[str] = []
    recommendation_by_operation = {
        "hard_negative": "collect_label_geometry_for_hard_negative_synthesis",
        "add_line_item": "collect_cross_receipt_item_catalog_for_add_item_synthesis",
        "remove_line_item": "collect_multi_item_receipts_for_remove_item_synthesis",
        "replace_field": "collect_stable_date_time_examples_for_field_replacement",
    }
    for operation in SYNTHESIS_OPERATION_FAMILIES:
        ready_merchants = [
            str(row.get("merchant_name") or "Unknown merchant")
            for row in rows
            if _row_operation_ready(row, operation)
        ]
        blocked = []
        for row in rows:
            if _row_operation_ready(row, operation):
                continue
            reasons = _operation_gap_reasons(row, operation)
            if reasons:
                blocked.append(
                    {
                        "merchant_name": str(
                            row.get("merchant_name") or "Unknown merchant"
                        ),
                        "reasons": reasons,
                    }
                )
        operation_count = sum(
            _operation_counts_for_row(row).get(operation, 0) for row in rows
        )
        ready_count = len(ready_merchants)
        if merchant_count and not ready_count:
            recommendations.append(recommendation_by_operation[operation])
        operations[operation] = {
            "ready_merchant_count": ready_count,
            "merchant_count": merchant_count,
            "ready_share": _ratio(ready_count, merchant_count),
            "candidate_count": operation_count,
            "ready_merchants": ready_merchants[:10],
            "blocked_merchants": blocked[:10],
        }

    ready_operation_count = sum(
        1
        for operation in operations.values()
        if _safe_int(operation.get("ready_merchant_count"))
    )
    return {
        "operation_count": len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": ready_operation_count,
        "ready_operation_share": _ratio(
            ready_operation_count,
            len(SYNTHESIS_OPERATION_FAMILIES),
        ),
        "operations": operations,
        "recommendations": recommendations[:8],
    }


def _merchant_gap_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    merchant_gaps: list[dict[str, Any]] = []
    blocker_counts: dict[str, int] = {}
    limitation_counts: dict[str, int] = {}
    for row in rows:
        merchant = str(row.get("merchant_name") or "Unknown merchant")
        missing_operations = [
            operation
            for operation in SYNTHESIS_OPERATION_FAMILIES
            if not _row_operation_ready(row, operation)
        ]
        operation_gap_reasons = {
            operation: _operation_gap_reasons(row, operation)
            for operation in missing_operations
        }
        blockers = [str(item) for item in row.get("blockers") or [] if item]
        limitations = [str(item) for item in row.get("limitations") or [] if item]
        for blocker in blockers:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        for limitation in limitations:
            limitation_counts[limitation] = limitation_counts.get(limitation, 0) + 1
        status = str(row.get("readiness_status") or row.get("status") or "missing")
        accepted_count = _safe_int(row.get("accepted_count"))
        candidate_count = _safe_int(row.get("candidate_count")) or 0
        if accepted_count is None:
            # Candidate operation counts are pre-selection sketches; only accepted
            # operation counts can stand in for accepted training examples.
            accepted_count = sum(_accepted_operation_counts_for_row(row).values())
        merchant_gaps.append(
            {
                "merchant_name": merchant,
                "status": status,
                "score": _safe_float(row.get("readiness_score") or row.get("score")),
                "candidate_count": candidate_count,
                "accepted_count": accepted_count,
                "ready_operation_count": len(SYNTHESIS_OPERATION_FAMILIES)
                - len(missing_operations),
                "missing_operations": missing_operations,
                "operation_gap_reasons": operation_gap_reasons,
                "blockers": blockers[:5],
                "limitations": limitations[:5],
            }
        )

    merchant_gaps.sort(
        key=lambda row: (
            row["status"] == "ready",
            row["accepted_count"] > 0,
            row["ready_operation_count"],
            row["score"] or 0,
            row["merchant_name"],
        )
    )
    return {
        "blocked_merchant_count": sum(
            1 for row in merchant_gaps if row["status"] not in {"ready", "partial"}
        ),
        "merchant_gap_count": sum(
            1 for row in merchant_gaps if row["missing_operations"]
        ),
        "top_blockers": dict(
            sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ),
        "top_limitations": dict(
            sorted(limitation_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ),
        "merchants": merchant_gaps[:25],
    }


def summarize_local_synthesis_preflight(
    artifacts: list[dict[str, Any]],
    *,
    min_ready_share: float = 0.7,
    min_avg_readiness_score: float = 0.7,
    min_grounded_candidate_share: float = 0.5,
) -> dict[str, Any]:
    """Summarize local pattern artifacts before launching paid replay/training."""
    merchant_rows = [_compact_readiness_artifact(artifact) for artifact in artifacts]
    operation_coverage = _operation_coverage_from_rows(merchant_rows)
    merchant_gap_summary = _merchant_gap_summary(merchant_rows)
    merchant_count = len(merchant_rows)
    readiness_status_counts: dict[str, int] = {}
    source_quality_status_counts: dict[str, int] = {}
    for row in merchant_rows:
        status = str(row["readiness_status"])
        readiness_status_counts[status] = readiness_status_counts.get(status, 0) + 1
        source_status = row.get("source_quality_status")
        if source_status:
            source_status = str(source_status)
            source_quality_status_counts[source_status] = (
                source_quality_status_counts.get(source_status, 0) + 1
            )

    ready_merchant_count = readiness_status_counts.get("ready", 0)
    blocked_source_quality_count = source_quality_status_counts.get("blocked", 0)
    readiness_scores = [
        row["readiness_score"]
        for row in merchant_rows
        if row.get("readiness_score") is not None
    ]
    candidate_count = sum(row["candidate_count"] for row in merchant_rows)
    grounded_candidate_count = sum(
        row["grounded_candidate_count"] for row in merchant_rows
    )
    arithmetic_candidate_count = sum(
        row["arithmetic_candidate_count"] for row in merchant_rows
    )
    ready_share = (
        round(ready_merchant_count / merchant_count, 3) if merchant_count else 0.0
    )
    avg_readiness_score = (
        round(sum(readiness_scores) / len(readiness_scores), 3)
        if readiness_scores
        else None
    )
    grounded_candidate_share = (
        round(grounded_candidate_count / candidate_count, 3) if candidate_count else 0.0
    )

    reasons: list[str] = []
    if not merchant_count:
        reasons.append("no_pattern_artifacts")
    if blocked_source_quality_count:
        reasons.append("source_receipt_quality_blocked")
    if len(readiness_scores) < merchant_count:
        reasons.append("missing_synthesis_readiness")
    if ready_share < min_ready_share:
        reasons.append("ready_merchant_share_below_threshold")
    if avg_readiness_score is None or avg_readiness_score < min_avg_readiness_score:
        reasons.append("avg_readiness_score_below_threshold")
    if not candidate_count:
        reasons.append("no_synthetic_candidates")
    elif grounded_candidate_share < min_grounded_candidate_share:
        reasons.append("grounded_candidate_share_below_threshold")

    return {
        "ready": not reasons,
        "reasons": reasons,
        "thresholds": {
            "min_ready_share": min_ready_share,
            "min_avg_readiness_score": min_avg_readiness_score,
            "min_grounded_candidate_share": min_grounded_candidate_share,
        },
        "merchant_count": merchant_count,
        "ready_merchant_count": ready_merchant_count,
        "ready_share": ready_share,
        "avg_readiness_score": avg_readiness_score,
        "readiness_status_counts": readiness_status_counts,
        "source_quality_status_counts": source_quality_status_counts,
        "blocked_source_quality_merchant_count": blocked_source_quality_count,
        "candidate_count": candidate_count,
        "grounded_candidate_count": grounded_candidate_count,
        "grounded_candidate_share": grounded_candidate_share,
        "arithmetic_candidate_count": arithmetic_candidate_count,
        "llm_execution": _summarize_llm_execution(merchant_rows),
        "operation_coverage": operation_coverage,
        "merchant_gap_summary": merchant_gap_summary,
        "merchants": merchant_rows[:25],
    }


def load_local_pattern_artifacts(
    *,
    artifact_files: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load local pattern-builder JSON artifacts without touching AWS."""
    artifacts: list[dict[str, Any]] = []
    for path in _local_json_paths(
        artifact_files=artifact_files,
        artifact_dirs=artifact_dirs,
    ):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(f"Pattern artifact must be a JSON object: {path}")
        artifacts.append(data)
    return artifacts


def _local_json_paths(
    *,
    artifact_files: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    for value in artifact_files or []:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.json")))
        else:
            paths.append(path)
    for value in artifact_dirs or []:
        paths.extend(sorted(Path(value).rglob("*.json")))
    return paths


def _slug(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in slug.split("-") if part) or "merchant"


def _default_false_positive_recipe(label: str) -> dict[str, Any]:
    normalized = label.upper()
    return {
        "recipe_id": f"offline-o-{normalized.lower()}",
        "actual_label": "O",
        "predicted_label": normalized,
        "error_kind": "false_positive",
        "objective": f"Add merchant-local O hard negatives near {normalized}.",
        "merchant_scope": "same_merchant",
        "target_zone": {
            "label": normalized,
            "y_band": "observed",
            "x_zone": "observed",
        },
        "source_examples": [],
        "retrieval_queries": [],
        "layout_constraints": [],
        "mutation_steps": [],
        "expected_label_effect": f"Improve {normalized} precision.",
        "safeguards": ["train_only", "real_receipts_only_validation"],
    }


def _default_synthetic_plan(
    merchant_name: str,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = [
        "ADDRESS_LINE",
        "DISCOUNT",
        "GRAND_TOTAL",
        "LINE_TOTAL",
        "MERCHANT_NAME",
    ]
    return {
        "merchant_name": merchant_name,
        "source_receipt_count": len(receipts),
        "confusion_target_count": len(labels),
        "recipes": [_default_false_positive_recipe(label) for label in labels],
        "synthetic_receipt_guidance": [
            "Offline deterministic plan; no LLM call was used.",
            "Prefer merchant-local geometry and observed item evidence.",
        ],
        "similar_merchant_mining": {},
        "metric_guardrails": [
            "validation_split=real_receipts_only",
            "cap_synthetic_examples_per_merchant",
        ],
        "overtraining_guards": [
            "LayoutLM loader applies quality gates and merchant/operation caps.",
        ],
    }


def _install_temporary_receipt_agent_stub(
    saved_modules: dict[str, Any],
    name: str,
    *,
    package_path: Path,
) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(package_path)]  # type: ignore[attr-defined]
    saved_modules[name] = sys.modules.get(name, _MISSING_MODULE)
    sys.modules[name] = module
    return module


def _load_lightweight_label_evaluator_module(
    module_name: str,
    *,
    saved_modules: dict[str, Any],
):
    qualified_name = f"receipt_agent.agents.label_evaluator.{module_name}"
    saved_modules[qualified_name] = sys.modules.get(
        qualified_name,
        _MISSING_MODULE,
    )
    path = (
        PROJECT_ROOT
        / "receipt_agent"
        / "receipt_agent"
        / "agents"
        / "label_evaluator"
        / f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(qualified_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load local synthesis module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


def _restore_modules(saved_modules: dict[str, Any]) -> None:
    # Restore leaf modules before parent package stubs.
    for name, previous in reversed(saved_modules.items()):
        if previous is _MISSING_MODULE:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _load_local_synthesis_functions() -> dict[str, Any]:
    """Load deterministic synthesis modules without importing the full agent."""
    saved_modules: dict[str, Any] = {}
    try:
        receipt_agent = _install_temporary_receipt_agent_stub(
            saved_modules,
            "receipt_agent",
            package_path=PROJECT_ROOT / "receipt_agent" / "receipt_agent",
        )
        agents = _install_temporary_receipt_agent_stub(
            saved_modules,
            "receipt_agent.agents",
            package_path=(PROJECT_ROOT / "receipt_agent" / "receipt_agent" / "agents"),
        )
        label_evaluator = _install_temporary_receipt_agent_stub(
            saved_modules,
            "receipt_agent.agents.label_evaluator",
            package_path=(
                PROJECT_ROOT
                / "receipt_agent"
                / "receipt_agent"
                / "agents"
                / "label_evaluator"
            ),
        )
        receipt_agent.agents = agents
        agents.label_evaluator = label_evaluator

        merchant = _load_lightweight_label_evaluator_module(
            "merchant_synthesis",
            saved_modules=saved_modules,
        )
        label_evaluator.merchant_synthesis = merchant
        sprouts = _load_lightweight_label_evaluator_module(
            "sprouts_parameterization",
            saved_modules=saved_modules,
        )
        label_evaluator.sprouts_parameterization = sprouts

        return {
            "build_merchant_synthesis_profile": (
                merchant.build_merchant_synthesis_profile
            ),
            "generate_merchant_synthesis_candidates": (
                merchant.generate_merchant_synthesis_candidates
            ),
            "generate_arithmetic_sprouts_candidates": (
                sprouts.generate_arithmetic_sprouts_candidates
            ),
            "generate_parameterized_sprouts_candidates": (
                sprouts.generate_parameterized_sprouts_candidates
            ),
            "is_sprouts_merchant": sprouts.is_sprouts_merchant,
        }
    finally:
        _restore_modules(saved_modules)


def _generate_local_synthesis_candidates(
    synthesis_functions: dict[str, Any],
    plan: dict[str, Any],
    receipts: list[dict[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    merchant_name = str(plan.get("merchant_name") or "Unknown merchant")
    if synthesis_functions["is_sprouts_merchant"](merchant_name):
        # Scale with the requested budget instead of a flat 5 so a rich
        # merchant can yield its full synthetic volume. Arithmetic add/remove
        # candidates lead: they are arithmetic-reconciled, layout-preserving
        # edits that clear the loader's structure-similarity gate, whereas
        # parameterized hard negatives insert a synthetic O row and most are
        # rejected for low structure similarity. Leading with arithmetic stops
        # the hard negatives from consuming the budget and starving the
        # high-fidelity candidates; the remainder still funds hard-negative
        # label-confusion coverage.
        candidate_limit = max(1, max_candidates)
        rows = list(
            synthesis_functions["generate_arithmetic_sprouts_candidates"](
                receipts,
                max_candidates=candidate_limit,
            )
        )
        remaining = candidate_limit - len(rows)
        if remaining > 0:
            rows.extend(
                synthesis_functions[
                    "generate_parameterized_sprouts_candidates"
                ](
                    plan,
                    receipts,
                    max_candidates=remaining,
                )
            )
        return [dict(row) for row in rows[:candidate_limit]]

    return [
        dict(row)
        for row in synthesis_functions["generate_merchant_synthesis_candidates"](
            plan,
            receipts,
            max_candidates=max_candidates,
        )
    ]


# Boxes are stored either as 0..1 fractions or as 0..1000 pixel coordinates.
# A fractional box that touches the receipt edge legitimately reaches slightly
# past 1.0 (a full-width header's x+width, or a top/bottom word's y+height can
# be ~1.002), so a strict ``<= 1.0`` test misreads those fractions as pixels and
# collapses them to a degenerate 1x1 box. Pixel coordinates start in the tens,
# so 1.5 cleanly separates the two encodings while tolerating edge overflow.
_NORMALIZED_BBOX_MAX = 1.5


def _normalize_bbox(value: Any) -> list[int] | None:
    if isinstance(value, list) and len(value) == 4:
        coords = [_safe_float(coord) for coord in value]
        if all(coord is not None for coord in coords):
            max_coord = max(abs(coord or 0.0) for coord in coords)
            scale = 1000 if max_coord <= _NORMALIZED_BBOX_MAX else 1
            return [
                max(0, min(1000, int(round((coord or 0.0) * scale))))
                for coord in coords
            ]
    if isinstance(value, dict):
        x = _safe_float(value.get("x"))
        y = _safe_float(value.get("y"))
        width = _safe_float(value.get("width"))
        height = _safe_float(value.get("height"))
        if None not in (x, y, width, height):
            coords = [
                x or 0.0,
                y or 0.0,
                (x or 0.0) + (width or 0.0),
                (y or 0.0) + (height or 0.0),
            ]
            scale = (
                1000
                if max(abs(coord) for coord in coords) <= _NORMALIZED_BBOX_MAX
                else 1
            )
            return [max(0, min(1000, int(round(coord * scale)))) for coord in coords]
    return None


def _line_bbox(line: dict[str, Any]) -> list[int] | None:
    for key in ("bbox", "bounding_box"):
        if bbox := _normalize_bbox(line.get(key)):
            return bbox
    words = [word for word in line.get("words") or [] if isinstance(word, dict)]
    boxes = [
        bbox
        for word in words
        if (bbox := _normalize_bbox(word.get("bbox") or word.get("bounding_box")))
    ]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _split_line_text_words(
    text: str,
    *,
    bbox: list[int] | None,
    labels: list[str],
) -> list[dict[str, Any]]:
    tokens = [token for token in text.split() if token]
    if not tokens:
        return []
    if bbox is None:
        bbox = [0, 0, 1000, 24]
    x0, y0, x1, y1 = bbox
    width = max(1, x1 - x0)
    slot = max(1, width // len(tokens))
    words = []
    cursor = x0
    for index, token in enumerate(tokens):
        next_x = x1 if index == len(tokens) - 1 else min(x1, cursor + slot)
        words.append(
            {
                "text": token,
                "bbox": [cursor, y0, max(cursor + 1, next_x), y1],
                "labels": labels,
            }
        )
        cursor = next_x
    return words


def _normalize_local_line(
    line: dict[str, Any],
    *,
    fallback_line_id: int,
) -> dict[str, Any]:
    normalized = dict(line)
    normalized.setdefault("line_id", fallback_line_id)
    labels = [
        str(label) for label in normalized.get("labels") or [] if str(label).strip()
    ]
    words: list[dict[str, Any]] = []
    for word in normalized.get("words") or []:
        if not isinstance(word, dict) or not word.get("text"):
            continue
        next_word = dict(word)
        bbox = _normalize_bbox(word.get("bbox") or word.get("bounding_box"))
        if bbox:
            next_word["bbox"] = bbox
        next_word["labels"] = [
            str(label)
            for label in next_word.get("labels") or labels
            if str(label).strip()
        ]
        next_word.setdefault("line_id", normalized["line_id"])
        words.append(next_word)

    if not words:
        text = str(normalized.get("text") or "").strip()
        words = _split_line_text_words(
            text,
            bbox=_line_bbox(normalized),
            labels=labels,
        )
        for word in words:
            word.setdefault("line_id", normalized["line_id"])

    if words:
        normalized["words"] = words
    if bbox := _line_bbox(normalized):
        normalized["bbox"] = bbox
    if "y" not in normalized:
        bbox = normalized.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            normalized["y"] = round(((bbox[1] + bbox[3]) / 2) / 1000, 4)
    return normalized


def _normalize_local_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(receipt)
    lines = [
        _normalize_local_line(line, fallback_line_id=index)
        for index, line in enumerate(normalized.get("lines") or [], start=1)
        if isinstance(line, dict)
    ]
    if lines:
        normalized["lines"] = lines
    return normalized


def _id_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def _receipt_identity(receipt: dict[str, Any]) -> tuple[str, str]:
    return (
        _id_value(receipt, "image_id"),
        _id_value(receipt, "receipt_id", "receipt_num"),
    )


def _record_matches_receipt(record: dict[str, Any], receipt: dict[str, Any]) -> bool:
    receipt_image, receipt_id = _receipt_identity(receipt)
    record_image = _id_value(record, "image_id")
    record_receipt = _id_value(record, "receipt_id", "receipt_num")
    return (
        not receipt_image or not record_image or record_image == receipt_image
    ) and (not receipt_id or not record_receipt or record_receipt == receipt_id)


def _record_line_id(record: dict[str, Any]) -> str:
    return _id_value(record, "line_id")


def _record_word_id(record: dict[str, Any]) -> str:
    return _id_value(record, "word_id")


def _valid_label_record(record: dict[str, Any]) -> bool:
    status = str(record.get("validation_status") or record.get("status") or "").upper()
    return status not in {"INVALID", "NEEDS_REVIEW", "REJECTED"}


def _payload_label_lookup(
    payload: dict[str, Any],
) -> dict[tuple[str, str, str, str], list[str]]:
    lookup: dict[tuple[str, str, str, str], list[str]] = {}
    for key in ("word_labels", "receipt_word_labels", "labels"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("label"):
                continue
            if not _record_line_id(row) or not _record_word_id(row):
                continue
            if not _valid_label_record(row):
                continue
            label_key = (
                _id_value(row, "image_id"),
                _id_value(row, "receipt_id", "receipt_num"),
                _record_line_id(row),
                _record_word_id(row),
            )
            lookup.setdefault(label_key, []).append(str(row["label"]).upper())
    return lookup


def _labels_for_word(
    word: dict[str, Any],
    *,
    receipt: dict[str, Any],
    label_lookup: dict[tuple[str, str, str, str], list[str]],
) -> list[str]:
    image_id, receipt_id = _receipt_identity(receipt)
    line_id = _record_line_id(word)
    word_id = _record_word_id(word)
    candidates = [
        (
            _id_value(word, "image_id") or image_id,
            _id_value(word, "receipt_id", "receipt_num") or receipt_id,
            line_id,
            word_id,
        ),
        (image_id, receipt_id, line_id, word_id),
        ("", "", line_id, word_id),
    ]
    labels: list[str] = []
    for key in candidates:
        for label in label_lookup.get(key, []):
            if label not in labels:
                labels.append(label)
    return labels


def _payload_words_for_receipt(
    payload: dict[str, Any],
    receipt: dict[str, Any],
    *,
    label_lookup: dict[tuple[str, str, str, str], list[str]],
) -> dict[str, list[dict[str, Any]]]:
    words_by_line: dict[str, list[dict[str, Any]]] = {}
    # Prefer the receipt-cropped word set. An ``export_image`` dump carries BOTH
    # an image-level ``words`` list and a receipt-level ``receipt_words`` list
    # that describe the SAME words in DIFFERENT coordinate frames (whole image
    # vs the receipt crop). Unioning them double-counts every word and collides
    # boxes from incompatible frames, producing hundreds of spurious overlaps
    # that destroy the layout-integrity geometry. Try the receipt-level rows
    # first; fall back to image-level ``words`` PER RECEIPT — so a receipt that
    # has no receipt-level rows (even when other receipts in the export do) still
    # gets its image-level words instead of being dropped.
    for source_key in ("receipt_words", "words"):
        rows = payload.get(source_key)
        if not isinstance(rows, list):
            continue
        candidate: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, dict) or not row.get("text"):
                continue
            if not _record_line_id(row) or not _record_matches_receipt(
                row,
                receipt,
            ):
                continue
            word = dict(row)
            if bbox := _normalize_bbox(row.get("bbox") or row.get("bounding_box")):
                word["bbox"] = bbox
            labels = list(word.get("labels") or [])
            for label in _labels_for_word(
                word,
                receipt=receipt,
                label_lookup=label_lookup,
            ):
                if label not in labels:
                    labels.append(label)
            word["labels"] = labels
            candidate.setdefault(_record_line_id(row), []).append(word)
        if candidate:
            words_by_line = candidate
            break
    for words in words_by_line.values():
        words.sort(key=lambda word: _safe_int(word.get("word_id")) or 0)
    return words_by_line


def _merge_payload_words_into_line(
    line: dict[str, Any],
    words_by_line: dict[str, list[dict[str, Any]]],
    *,
    receipt: dict[str, Any],
    label_lookup: dict[tuple[str, str, str, str], list[str]],
) -> dict[str, Any]:
    line_id = _record_line_id(line)
    payload_words = words_by_line.get(line_id, [])
    if payload_words and not line.get("words"):
        next_line = dict(line)
        next_line["words"] = payload_words
        return next_line

    if not line.get("words"):
        return line

    next_line = dict(line)
    next_words = []
    for word in line.get("words") or []:
        if not isinstance(word, dict):
            continue
        next_word = dict(word)
        labels = list(next_word.get("labels") or [])
        for label in _labels_for_word(
            next_word,
            receipt=receipt,
            label_lookup=label_lookup,
        ):
            if label not in labels:
                labels.append(label)
        next_word["labels"] = labels
        next_words.append(next_word)
    next_line["words"] = next_words
    return next_line


def _payload_canonical_merchants(
    payload: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """Map ``(image_id, receipt_id) -> canonical merchant name`` from places.

    Keyed to match ``_receipt_identity`` so a receipt can look up its Google
    Places merchant name. Only non-empty names are recorded.
    """
    out: dict[tuple[str, str], str] = {}
    for key in ("receipt_places", "places"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("merchant_name") or "").strip()
            if not name:
                continue
            out[
                (
                    _id_value(row, "image_id"),
                    _id_value(row, "receipt_id", "receipt_num"),
                )
            ] = name
    return out


def _payload_place_lookup(
    payload: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Map ``(image_id, receipt_id) -> cached Google Places record``.

    Keyed to match ``_receipt_identity`` so each receipt can attach its own
    store's place record (address / phone / website / place_id). Image-level
    rows that carry no receipt_id are keyed under ``(image_id, "")``.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key in ("receipt_places", "places"):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("place_id"):
                continue
            out[
                (
                    _id_value(row, "image_id"),
                    _id_value(row, "receipt_id", "receipt_num"),
                )
            ] = row
    return out


def _attach_payload_lines(
    receipts: list[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    # Prefer receipt-level lines for the same reason as words: image-level
    # ``lines`` are in the whole-image frame while ``receipt_lines`` match the
    # receipt crop the words use. Mixing frames misplaces lines vertically. The
    # choice is made PER RECEIPT (below), not globally, so a receipt that lacks
    # receipt-level lines but has image-level lines still gets them instead of
    # being dropped — the same per-receipt fallback the word loader uses.
    receipt_level_lines = [
        line
        for line in (payload.get("receipt_lines") or [])
        if isinstance(line, dict)
    ]
    image_level_lines = [
        line for line in (payload.get("lines") or []) if isinstance(line, dict)
    ]

    def _matching_lines(
        lines: list[dict[str, Any]], image_id: str, receipt_id: str
    ) -> list[dict[str, Any]]:
        return [
            line
            for line in lines
            if (not image_id or str(line.get("image_id") or "") in {"", image_id})
            and (
                not receipt_id
                or str(line.get("receipt_id") or line.get("receipt_num") or "")
                in {"", receipt_id}
            )
        ]

    label_lookup = _payload_label_lookup(payload)
    # Canonical Google Places merchant name (from ``receipt_places``) is the
    # clean per-chain grouping key: every receipt of a chain shares it, whereas
    # MERCHANT_NAME-label / header-text inference fragments one chain into many
    # variants ("SPROUTS", "001 SPROUTS", "5.99 SPROUTS"). Attach it so the
    # merchant grouping does not splinter the same merchant across receipts.
    canonical_merchant = _payload_canonical_merchants(payload)
    place_lookup = _payload_place_lookup(payload)
    # The full set of cached store records for this payload — including sibling
    # branches fetched via Places that are not tied to any receipt. This is the
    # store-header composition pool (the generator filters it by merchant match).
    merchant_place_pool = [
        row
        for key in ("receipt_places", "places")
        for row in (payload.get(key) or [])
        if isinstance(row, dict) and row.get("place_id")
    ]

    normalized_receipts: list[dict[str, Any]] = []
    for receipt in receipts:
        next_receipt = dict(receipt)
        if merchant_place_pool:
            next_receipt["merchant_place_pool"] = merchant_place_pool
        words_by_line = _payload_words_for_receipt(
            payload,
            receipt,
            label_lookup=label_lookup,
        )
        image_id, receipt_id = _receipt_identity(receipt)
        # The canonical Google Places name is the authoritative grouping key, so
        # it OVERRIDES any pre-existing (often noisy, header-derived) per-receipt
        # merchant_name; otherwise receipts sharing the same place can still be
        # split across merchants by their differing header text.
        # Image-level place rows are keyed under (image_id, "") because they
        # carry no receipt_id; fall back to that so the canonical Google Places
        # name is still applied instead of fragmenting on header-derived names.
        canonical = canonical_merchant.get(
            (image_id, receipt_id)
        ) or canonical_merchant.get((image_id, ""))
        if canonical:
            next_receipt["merchant_name"] = canonical
        # Attach the receipt's cached Google Places record (its own store) so
        # downstream synthesis can read its place_id and swap in a sibling
        # branch's store cluster. Image-level rows are keyed under (image_id, "").
        place_record = place_lookup.get((image_id, receipt_id)) or place_lookup.get(
            (image_id, "")
        )
        if place_record:
            next_receipt["receipt_place"] = place_record
            next_receipt["place_id"] = str(place_record.get("place_id") or "") or None
        # Receipt-level lines first (crop frame, matching the words); fall back
        # to image-level lines for THIS receipt when it has no receipt-level
        # lines, so it is not left without geometry and dropped.
        matching_lines = _matching_lines(
            receipt_level_lines, image_id, receipt_id
        )
        if not matching_lines:
            matching_lines = _matching_lines(
                image_level_lines, image_id, receipt_id
            )
        if not matching_lines and len(receipts) == 1:
            matching_lines = receipt_level_lines or image_level_lines
        if matching_lines and not next_receipt.get("lines"):
            next_receipt["lines"] = matching_lines
        if next_receipt.get("lines"):
            next_receipt["lines"] = [
                _merge_payload_words_into_line(
                    line,
                    words_by_line,
                    receipt=receipt,
                    label_lookup=label_lookup,
                )
                for line in next_receipt.get("lines") or []
                if isinstance(line, dict)
            ]
        elif words_by_line:
            next_receipt["lines"] = [
                {"line_id": line_id, "words": words}
                for line_id, words in sorted(
                    words_by_line.items(),
                    key=lambda item: _safe_int(item[0]) or 0,
                )
            ]
        normalized_receipts.append(_normalize_local_receipt(next_receipt))
    return normalized_receipts


def _infer_merchant_from_receipt(receipt: dict[str, Any]) -> str | None:
    merchant = str(receipt.get("merchant_name") or "").strip()
    if merchant:
        return merchant

    for line in receipt.get("lines") or []:
        words = [
            word
            for word in line.get("words") or []
            if isinstance(word, dict)
            and "MERCHANT_NAME"
            in {str(label).upper() for label in word.get("labels") or []}
        ]
        text = " ".join(str(word.get("text") or "") for word in words).strip()
        if text:
            return text

    for line in (receipt.get("lines") or [])[:6]:
        text = str(line.get("text") or "").strip()
        if not text:
            text = " ".join(
                str(word.get("text") or "")
                for word in line.get("words") or []
                if isinstance(word, dict)
            ).strip()
        if _merchant_header_candidate(text):
            return text
    return None


def _merchant_header_candidate(text: str) -> bool:
    stripped = " ".join(text.split()).strip(" -")
    if not stripped or len(stripped) < 3:
        return False
    upper = stripped.upper()
    if any(ch.isdigit() for ch in upper):
        return False
    if any(
        marker in upper
        for marker in ("PHONE", "ORDER", "SERVER", "CASHIER", "REGISTER")
    ):
        return False
    if any(ch in upper for ch in (":", "#", "$", "@")):
        return False
    if len(stripped.split()) > 5:
        return False
    return True


def _group_receipts_by_merchant(
    receipts: list[dict[str, Any]],
    *,
    default_merchant: str | None = None,
    source: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        merchant = (
            default_merchant
            or _infer_merchant_from_receipt(receipt)
            or "Unknown merchant"
        )
        grouped.setdefault(str(merchant), []).append(receipt)
    return [
        {
            "merchant_name": merchant,
            "receipts": rows,
            "source_files": [source],
        }
        for merchant, rows in sorted(grouped.items())
    ]


def _receipt_groups_from_payload(
    payload: Any,
    *,
    source: str,
) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("merchants"), list):
            groups: list[dict[str, Any]] = []
            for item in payload["merchants"]:
                groups.extend(_receipt_groups_from_payload(item, source=source))
            return groups
        if isinstance(payload.get("receipts"), list) or isinstance(
            payload.get("receipts_data"), list
        ):
            receipts = payload.get("receipts") or payload.get("receipts_data") or []
            normalized_receipts = _attach_payload_lines(
                [row for row in receipts if isinstance(row, dict)],
                payload,
            )
            return _group_receipts_by_merchant(
                normalized_receipts,
                default_merchant=(
                    str(payload.get("merchant_name"))
                    if payload.get("merchant_name")
                    else None
                ),
                source=source,
            )
        if payload and all(isinstance(value, list) for value in payload.values()):
            return [
                {
                    "merchant_name": str(merchant),
                    "receipts": [
                        _normalize_local_receipt(row)
                        for row in receipts
                        if isinstance(row, dict)
                    ],
                    "source_files": [source],
                }
                for merchant, receipts in payload.items()
            ]
    if isinstance(payload, list):
        if all(isinstance(item, dict) and "merchant_name" in item for item in payload):
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in payload:
                merchant = str(item.get("merchant_name") or "Unknown merchant")
                if isinstance(item.get("receipts"), list):
                    grouped.setdefault(merchant, []).extend(
                        _normalize_local_receipt(row)
                        for row in item["receipts"]
                        if isinstance(row, dict)
                    )
                else:
                    grouped.setdefault(merchant, []).append(
                        _normalize_local_receipt(item)
                    )
            return [
                {
                    "merchant_name": merchant,
                    "receipts": receipts,
                    "source_files": [source],
                }
                for merchant, receipts in grouped.items()
            ]
    return []


def load_local_receipt_groups(
    *,
    receipt_files: list[str] | None = None,
    receipt_dirs: list[str] | None = None,
) -> list[dict[str, Any]]:
    groups_by_merchant: dict[str, dict[str, Any]] = {}
    for path in _local_json_paths(
        artifact_files=receipt_files,
        artifact_dirs=receipt_dirs,
    ):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for group in _receipt_groups_from_payload(payload, source=str(path)):
            merchant = str(group["merchant_name"])
            existing = groups_by_merchant.setdefault(
                merchant,
                {
                    "merchant_name": merchant,
                    "receipts": [],
                    "source_files": [],
                },
            )
            existing["receipts"].extend(group["receipts"])
            existing["source_files"].extend(group["source_files"])
    return sorted(groups_by_merchant.values(), key=lambda row: row["merchant_name"])


def _receipt_label_counts(receipt: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in receipt.get("lines") or []:
        if not isinstance(line, dict):
            continue
        for word in line.get("words") or []:
            if not isinstance(word, dict):
                continue
            for label in word.get("labels") or []:
                normalized = str(label).upper().strip()
                if normalized:
                    counts[normalized] = counts.get(normalized, 0) + 1
    return counts


PRICE_TEXT_RE = re.compile(r"(?:^|\s)\$?\d{1,4}(?:,\d{3})*\.\d{2}\b")
DATE_TIME_TEXT_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}:\d{2}\s?(?:AM|PM)?)\b",
    re.IGNORECASE,
)
TOTAL_TEXT_MARKERS = {
    "AMOUNT",
    "BALANCE",
    "DUE",
    "GRAND TOTAL",
    "SUBTOTAL",
    "TOTAL",
}
LINE_ITEM_EXCLUDED_TEXT_MARKERS = TOTAL_TEXT_MARKERS | {
    "CARD",
    "CASH",
    "CHANGE",
    "PAYMENT",
    "TAX",
    "TIP",
}


def _normalized_line_text(line: dict[str, Any]) -> str:
    text = str(line.get("text") or "").strip()
    if text:
        return " ".join(text.split())
    return " ".join(
        str(word.get("text") or "")
        for word in line.get("words") or []
        if isinstance(word, dict) and word.get("text")
    ).strip()


def _line_has_price_like_text(text: str) -> bool:
    return bool(PRICE_TEXT_RE.search(text))


def _line_has_total_like_text(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in TOTAL_TEXT_MARKERS) and (
        _line_has_price_like_text(text)
        or any(marker in upper for marker in {"TOTAL", "DUE"})
    )


def _line_has_line_item_like_text(text: str) -> bool:
    if not _line_has_price_like_text(text) or _line_has_total_like_text(text):
        return False
    upper = text.upper()
    if any(marker in upper for marker in LINE_ITEM_EXCLUDED_TEXT_MARKERS):
        return False
    prefix = PRICE_TEXT_RE.split(text, maxsplit=1)[0]
    return any(ch.isalpha() for ch in prefix) and len(prefix.split()) <= 8


def _line_has_date_time_like_text(text: str) -> bool:
    return bool(DATE_TIME_TEXT_RE.search(text))


def _receipt_text_structure_counts(receipt: dict[str, Any]) -> dict[str, int]:
    counts = {
        "merchant_header_like_line_count": 0,
        "price_like_line_count": 0,
        "line_item_like_text_line_count": 0,
        "total_like_text_line_count": 0,
        "date_time_like_text_line_count": 0,
    }
    for index, line in enumerate(
        [line for line in receipt.get("lines") or [] if isinstance(line, dict)]
    ):
        text = _normalized_line_text(line)
        if not text:
            continue
        if index < 6 and _merchant_header_candidate(text):
            counts["merchant_header_like_line_count"] += 1
        if _line_has_price_like_text(text):
            counts["price_like_line_count"] += 1
        if _line_has_line_item_like_text(text):
            counts["line_item_like_text_line_count"] += 1
        if _line_has_total_like_text(text):
            counts["total_like_text_line_count"] += 1
        if _line_has_date_time_like_text(text):
            counts["date_time_like_text_line_count"] += 1
    return counts


def _source_receipt_quality(
    merchant_name: str,
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    label_counts: dict[str, int] = {}
    receipt_count = len(receipts)
    receipts_with_lines = 0
    receipts_with_words = 0
    receipts_with_labels = 0
    receipts_with_merchant_name_label = 0
    receipts_with_line_item_labels = 0
    receipts_with_grand_total_label = 0
    receipts_with_date_or_time_label = 0
    receipts_with_line_item_like_text = 0
    receipts_with_total_like_text = 0
    receipts_with_date_time_like_text = 0
    receipts_with_merchant_header_like_text = 0
    total_line_count = 0
    total_word_count = 0
    text_structure_counts = {
        "merchant_header_like_line_count": 0,
        "price_like_line_count": 0,
        "line_item_like_text_line_count": 0,
        "total_like_text_line_count": 0,
        "date_time_like_text_line_count": 0,
    }

    for receipt in receipts:
        lines = [line for line in receipt.get("lines") or [] if isinstance(line, dict)]
        if lines:
            receipts_with_lines += 1
        total_line_count += len(lines)
        word_count = sum(
            len([word for word in line.get("words") or [] if isinstance(word, dict)])
            for line in lines
        )
        if word_count:
            receipts_with_words += 1
        total_word_count += word_count
        receipt_counts = _receipt_label_counts(receipt)
        for label, count in receipt_counts.items():
            label_counts[label] = label_counts.get(label, 0) + count
        if receipt_counts:
            receipts_with_labels += 1
        if receipt_counts.get("MERCHANT_NAME"):
            receipts_with_merchant_name_label += 1
        if receipt_counts.get("PRODUCT_NAME") and receipt_counts.get("LINE_TOTAL"):
            receipts_with_line_item_labels += 1
        if receipt_counts.get("GRAND_TOTAL"):
            receipts_with_grand_total_label += 1
        if receipt_counts.get("DATE") or receipt_counts.get("TIME"):
            receipts_with_date_or_time_label += 1
        receipt_text_counts = _receipt_text_structure_counts(receipt)
        for key, count in receipt_text_counts.items():
            text_structure_counts[key] += count
        if receipt_text_counts["line_item_like_text_line_count"]:
            receipts_with_line_item_like_text += 1
        if receipt_text_counts["total_like_text_line_count"]:
            receipts_with_total_like_text += 1
        if receipt_text_counts["date_time_like_text_line_count"]:
            receipts_with_date_time_like_text += 1
        if receipt_text_counts["merchant_header_like_line_count"]:
            receipts_with_merchant_header_like_text += 1

    labeled_word_count = sum(label_counts.values())
    text_structure_status = "not_applicable"
    if not labeled_word_count and total_word_count:
        if (
            text_structure_counts["line_item_like_text_line_count"]
            or text_structure_counts["total_like_text_line_count"]
            or text_structure_counts["price_like_line_count"]
        ):
            text_structure_status = "recoverable_unlabeled_text"
        else:
            text_structure_status = "unlabeled_text_without_receipt_structure"
    elif labeled_word_count:
        text_structure_status = "labeled"

    blockers: list[str] = []
    limitations: list[str] = []
    if not receipt_count:
        blockers.append("no_receipts")
    if not receipts_with_lines:
        blockers.append("no_receipt_lines")
    if not total_word_count:
        blockers.append("no_receipt_words")
    if not labeled_word_count:
        blockers.append("no_word_labels")
        if text_structure_status == "recoverable_unlabeled_text":
            limitations.append("unlabeled_text_requires_label_validation")
    if not receipts_with_line_item_labels:
        limitations.append("no_labeled_line_items")
    if not receipts_with_grand_total_label:
        limitations.append("no_grand_total_labels")
    if receipt_count < 2:
        limitations.append("single_receipt_limits_cross_receipt_grounding")

    status = "usable"
    if blockers:
        status = "blocked"
    elif limitations:
        status = "limited"

    top_labels = dict(
        sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
    )
    return {
        "merchant_name": merchant_name,
        "status": status,
        "receipt_count": receipt_count,
        "receipts_with_lines": receipts_with_lines,
        "receipts_with_words": receipts_with_words,
        "receipts_with_labels": receipts_with_labels,
        "receipts_with_merchant_name_label": receipts_with_merchant_name_label,
        "receipts_with_line_item_labels": receipts_with_line_item_labels,
        "receipts_with_grand_total_label": receipts_with_grand_total_label,
        "receipts_with_date_or_time_label": receipts_with_date_or_time_label,
        "receipts_with_line_item_like_text": receipts_with_line_item_like_text,
        "receipts_with_total_like_text": receipts_with_total_like_text,
        "receipts_with_date_time_like_text": receipts_with_date_time_like_text,
        "receipts_with_merchant_header_like_text": (
            receipts_with_merchant_header_like_text
        ),
        "line_count": total_line_count,
        "word_count": total_word_count,
        "labeled_word_count": labeled_word_count,
        "text_structure_status": text_structure_status,
        **text_structure_counts,
        "top_labels": top_labels,
        "blockers": blockers,
        "limitations": limitations,
    }


def summarize_source_receipt_quality(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    merchants = [
        row.get("source_receipt_quality")
        for row in rows
        if isinstance(row.get("source_receipt_quality"), dict)
    ]
    status_counts = _count_by(
        [str(row.get("status") or "missing") for row in merchants]
    )
    result = {
        "merchant_count": len(merchants),
        "usable_merchant_count": status_counts.get("usable", 0),
        "limited_merchant_count": status_counts.get("limited", 0),
        "blocked_merchant_count": status_counts.get("blocked", 0),
        "status_counts": status_counts,
        "receipt_count": sum(
            _safe_int(row.get("receipt_count")) or 0 for row in merchants
        ),
        "labeled_word_count": sum(
            _safe_int(row.get("labeled_word_count")) or 0 for row in merchants
        ),
        "merchants": merchants[:50],
    }
    if any(row.get("text_structure_status") for row in merchants):
        result["text_structure_status_counts"] = _count_by(
            [str(row.get("text_structure_status") or "missing") for row in merchants]
        )
        result["line_item_like_text_line_count"] = sum(
            _safe_int(row.get("line_item_like_text_line_count")) or 0
            for row in merchants
        )
        result["total_like_text_line_count"] = sum(
            _safe_int(row.get("total_like_text_line_count")) or 0 for row in merchants
        )
    return result


def build_local_pattern_artifacts_from_receipts(
    receipt_groups: list[dict[str, Any]],
    *,
    max_candidates: int = 5,
) -> list[dict[str, Any]]:
    """Build pattern-artifact JSON from local receipt structures without LLM."""
    synthesis_functions = _load_local_synthesis_functions()

    artifacts: list[dict[str, Any]] = []
    for group in receipt_groups:
        merchant_name = str(group.get("merchant_name") or "Unknown merchant")
        receipts = [row for row in group.get("receipts") or [] if isinstance(row, dict)]
        source_receipt_quality = _source_receipt_quality(merchant_name, receipts)
        plan = _default_synthetic_plan(merchant_name, receipts)
        profile = synthesis_functions["build_merchant_synthesis_profile"](
            merchant_name,
            receipts,
        )
        candidates = _generate_local_synthesis_candidates(
            synthesis_functions,
            plan,
            receipts,
            max_candidates=max_candidates,
        )
        artifacts.append(
            {
                "schema_version": "offline-pattern-artifact-v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "merchant_name": merchant_name,
                "source": "local_receipt_json",
                "source_files": list(dict.fromkeys(group.get("source_files") or [])),
                "source_receipt_count": len(receipts),
                "source_receipt_quality": source_receipt_quality,
                "synthetic_receipt_plan": plan,
                "merchant_receipt_parameterization": profile or {},
                "synthetic_receipt_candidates": candidates,
                "generation_notes": [
                    "Built locally from exported receipt structures.",
                    "No LLM, DynamoDB, AWS, or paid API call was used.",
                ],
            }
        )
    return artifacts


def write_local_pattern_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    output_dir: str,
) -> list[str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for artifact in artifacts:
        merchant = str(artifact.get("merchant_name") or "merchant")
        path = output / f"{_slug(merchant)}.json"
        path.write_text(
            json.dumps(artifact, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        written.append(str(path))
    return written


def summarize_built_pattern_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    output_files: list[str],
) -> dict[str, Any]:
    source_quality = summarize_source_receipt_quality(artifacts)
    return {
        "ready": bool(artifacts),
        "artifact_count": len(artifacts),
        "output_files": output_files,
        "merchant_count": len(
            {artifact.get("merchant_name") for artifact in artifacts}
        ),
        "candidate_count": sum(
            len(artifact.get("synthetic_receipt_candidates") or [])
            for artifact in artifacts
        ),
        "readiness_status_counts": _count_by(
            [
                str(
                    (artifact.get("merchant_receipt_parameterization") or {})
                    .get("synthesis_readiness", {})
                    .get("status")
                    or "missing"
                )
                for artifact in artifacts
            ]
        ),
        "source_receipt_quality": source_quality,
        "merchant_synthesis_audit": summarize_merchant_synthesis_audit(artifacts),
        "merchants": [
            {
                "merchant_name": artifact.get("merchant_name"),
                "source_receipt_count": artifact.get("source_receipt_count"),
                "source_quality_status": (
                    (artifact.get("source_receipt_quality") or {}).get("status")
                ),
                "candidate_count": len(
                    artifact.get("synthetic_receipt_candidates") or []
                ),
                "readiness_status": str(
                    (artifact.get("merchant_receipt_parameterization") or {})
                    .get("synthesis_readiness", {})
                    .get("status")
                    or "missing"
                ),
            }
            for artifact in artifacts[:25]
        ],
    }


def run_local_synthetic_pipeline(
    *,
    receipt_files: list[str] | None = None,
    receipt_dirs: list[str] | None = None,
    artifact_output_dir: str,
    bundle_output: str,
    max_candidates: int = 5,
    min_ready_share: float = 0.7,
    min_avg_readiness_score: float = 0.7,
    min_grounded_candidate_share: float = 0.5,
    min_structure_similarity: float = 0.6,
    max_per_merchant: int = 5,
    max_per_merchant_operation: int = 2,
) -> dict[str, Any]:
    """Run the full local receipt JSON -> artifact -> bundle proof path."""
    receipt_groups = load_local_receipt_groups(
        receipt_files=receipt_files,
        receipt_dirs=receipt_dirs,
    )
    artifacts = build_local_pattern_artifacts_from_receipts(
        receipt_groups,
        max_candidates=max_candidates,
    )
    artifact_files = write_local_pattern_artifacts(
        artifacts,
        output_dir=artifact_output_dir,
    )
    build_summary = summarize_built_pattern_artifacts(
        artifacts,
        output_files=artifact_files,
    )
    inventory = inventory_local_artifacts(artifact_dirs=[artifact_output_dir])
    preflight = summarize_local_synthesis_preflight(
        artifacts,
        min_ready_share=min_ready_share,
        min_avg_readiness_score=min_avg_readiness_score,
        min_grounded_candidate_share=min_grounded_candidate_share,
    )
    bundle = build_local_synthetic_training_bundle(
        artifacts,
        min_ready_share=min_ready_share,
        min_avg_readiness_score=min_avg_readiness_score,
        min_grounded_candidate_share=min_grounded_candidate_share,
        min_structure_similarity=min_structure_similarity,
        max_per_merchant=max_per_merchant,
        max_per_merchant_operation=max_per_merchant_operation,
        require_high_fidelity=True,
    )
    bundle_path = Path(bundle_output)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    candidate_mix_output = {
        "merchant_count": bundle["candidate_mix"]["merchant_count"],
        "accepted_merchant_count": bundle["candidate_mix"]["accepted_merchant_count"],
        "rejected_count": bundle["candidate_mix"]["rejected_count"],
        "rejection_reasons": bundle["candidate_mix"]["rejection_reasons"],
        "accepted_operation_counts": bundle["candidate_mix"][
            "accepted_operation_counts"
        ],
        "accepted_category_counts": bundle["candidate_mix"]["accepted_category_counts"],
        "accepted_field_replacement_counts": bundle["candidate_mix"][
            "accepted_field_replacement_counts"
        ],
        "accepted_structure_similarity": bundle["candidate_mix"][
            "accepted_structure_similarity"
        ],
        "accepted_structure_components": bundle["candidate_mix"].get(
            "accepted_structure_components"
        )
        or {},
        "accepted_mix_balance": bundle["candidate_mix"].get("accepted_mix_balance")
        or {},
        "merchants": list(bundle["candidate_mix"].get("merchants") or [])[:25],
    }
    if _safe_int(
        (bundle["candidate_mix"].get("accepted_real_baseline_comparison") or {}).get(
            "count"
        )
    ):
        candidate_mix_output["accepted_real_baseline_comparison"] = bundle[
            "candidate_mix"
        ]["accepted_real_baseline_comparison"]
    if bundle["candidate_mix"].get("accepted_candidate_quality"):
        candidate_mix_output["accepted_candidate_quality"] = bundle["candidate_mix"][
            "accepted_candidate_quality"
        ]
    if bundle["candidate_mix"].get("accepted_candidate_quality_components"):
        candidate_mix_output["accepted_candidate_quality_components"] = bundle[
            "candidate_mix"
        ]["accepted_candidate_quality_components"]
    if bundle["candidate_mix"].get("accepted_source_lineage"):
        candidate_mix_output["accepted_source_lineage"] = bundle["candidate_mix"][
            "accepted_source_lineage"
        ]

    return {
        "ready": bool(artifacts) and bool(bundle["ready"]),
        "reasons": list(bundle["reasons"]),
        "artifact_output_dir": str(Path(artifact_output_dir)),
        "bundle_output": str(bundle_path),
        "build": build_summary,
        "source_receipt_quality": build_summary.get("source_receipt_quality") or {},
        "inventory": {
            "ready": inventory["ready"],
            "file_count": inventory["file_count"],
            "kind_counts": inventory["kind_counts"],
            "preflightable_file_count": inventory["preflightable_file_count"],
            "layoutlm_ready_file_count": inventory["layoutlm_ready_file_count"],
            "candidate_count": inventory["candidate_count"],
            "merchant_count": inventory["merchant_count"],
            "recommendations": inventory["recommendations"],
        },
        "preflight": {
            "ready": preflight["ready"],
            "reasons": preflight["reasons"],
            "merchant_count": preflight["merchant_count"],
            "ready_merchant_count": preflight["ready_merchant_count"],
            "avg_readiness_score": preflight["avg_readiness_score"],
            "readiness_status_counts": preflight["readiness_status_counts"],
            "candidate_count": preflight["candidate_count"],
            "grounded_candidate_share": preflight["grounded_candidate_share"],
            "llm_execution": preflight.get("llm_execution") or {},
            "operation_coverage": preflight["operation_coverage"],
            "merchant_gap_summary": preflight.get("merchant_gap_summary") or {},
        },
        "bundle": {
            "ready": bundle["ready"],
            "reasons": bundle["reasons"],
            "selection": bundle["selection"],
            "training_batch_policy": (
                bundle.get("synthetic_training_batch_policy") or {}
            ),
            "merchant_synthesis_contract_count": len(
                bundle.get("merchant_synthesis_contracts") or []
            ),
            "merchant_synthesis_contracts": list(
                bundle.get("merchant_synthesis_contracts") or []
            )[:25],
            "candidate_mix": candidate_mix_output,
        },
        "report": bundle.get("synthesis_quality_report") or {},
    }


def _classify_local_artifact(data: dict[str, Any]) -> str:
    if data.get("schema_version") == "layoutlm-synthetic-training-bundle-v1":
        return "training_bundle"
    if isinstance(data.get("synthetic_receipt_candidates"), list):
        if isinstance(data.get("merchant_receipt_parameterization"), dict):
            return "pattern_artifact"
        return "candidate_container"
    if (
        isinstance(data.get("tokens"), list)
        and isinstance(data.get("bboxes"), list)
        and isinstance(data.get("ner_tags"), list)
        and isinstance(data.get("metadata"), dict)
    ):
        return "standalone_layoutlm_candidate"
    if (
        isinstance(data.get("lines"), list)
        and data.get("image_path")
        and isinstance(data.get("metadata"), dict)
    ):
        return "visual_receipt_summary"
    if data.get("candidate_id") and isinstance(data.get("metadata"), dict):
        return "standalone_candidate_metadata"
    return "unknown_json"


def _artifact_candidate_count(kind: str, data: dict[str, Any]) -> int:
    if kind in {"pattern_artifact", "candidate_container"}:
        return len(data.get("synthetic_receipt_candidates") or [])
    if kind == "training_bundle":
        selection = data.get("selection") or {}
        return _safe_int(selection.get("candidates_accepted")) or len(
            data.get("synthetic_training_examples") or []
        )
    if kind == "standalone_layoutlm_candidate":
        return 1
    return 0


def _artifact_operation_counts(kind: str, data: dict[str, Any]) -> dict[str, int]:
    if kind == "training_bundle":
        candidate_mix = data.get("candidate_mix") or {}
        accepted = candidate_mix.get("accepted_operation_counts") or {}
        if not isinstance(accepted, dict):
            return {}
        return {
            str(key): count
            for key, value in accepted.items()
            if (count := _safe_int(value)) is not None and count > 0
        }
    if kind in {"pattern_artifact", "candidate_container"}:
        return _count_by(
            [
                _candidate_operation(candidate)
                for candidate in data.get("synthetic_receipt_candidates") or []
                if isinstance(candidate, dict)
            ]
        )
    if kind in {"standalone_layoutlm_candidate", "standalone_candidate_metadata"}:
        return _count_by([_candidate_operation(data)])
    return {}


def _embedded_quality_report(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("synthesis_quality_report", "quality_report", "report"):
        report = data.get(key)
        if isinstance(report, dict):
            return report
    return {}


def _quality_report_inventory(report: dict[str, Any]) -> dict[str, Any]:
    if not report:
        return {
            "quality_report_present": False,
            "quality_report_ready": None,
            "quality_report_merchant_count": None,
            "quality_report_accepted_count": None,
            "quality_report_recommendations": [],
        }
    summary = report.get("summary") or {}
    return {
        "quality_report_present": True,
        "quality_report_ready": report.get("ready") is True,
        "quality_report_merchant_count": _safe_int(summary.get("merchant_count"))
        or len(report.get("merchants") or []),
        "quality_report_accepted_count": _safe_int(summary.get("accepted_count")),
        "quality_report_recommendations": [
            str(item) for item in (report.get("recommendations") or [])[:5]
        ],
    }


def _artifact_inventory_row(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    kind = _classify_local_artifact(data)
    candidate_mix = data.get("candidate_mix") or {}
    row = {
        "path": str(path),
        "kind": kind,
        "merchant_name": (
            _artifact_merchant(data)
            if kind in {"pattern_artifact", "candidate_container"}
            else (_candidate_merchant(data) if kind.startswith("standalone") else None)
        ),
        "candidate_count": _artifact_candidate_count(kind, data),
        "merchant_count": (
            _safe_int(candidate_mix.get("merchant_count"))
            if kind == "training_bundle"
            else None
        ),
        "accepted_merchant_count": (
            _safe_int(candidate_mix.get("accepted_merchant_count"))
            if kind == "training_bundle"
            else None
        ),
        "operation_counts": _artifact_operation_counts(kind, data),
        "preflightable": kind == "pattern_artifact",
        "layoutlm_ready": kind == "training_bundle",
    }
    if kind == "training_bundle":
        row.update(_quality_report_inventory(_embedded_quality_report(data)))
    return row


def inventory_local_artifacts(
    *,
    artifact_files: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """Inventory local JSON artifacts and classify synthesis readiness."""
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in _local_json_paths(
        artifact_files=artifact_files,
        artifact_dirs=artifact_dirs,
    ):
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                errors.append({"path": str(path), "error": "json_not_object"})
                continue
            rows.append(_artifact_inventory_row(path, data))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})

    kind_counts = _count_by([row["kind"] for row in rows])
    preflightable_count = sum(1 for row in rows if row["preflightable"])
    layoutlm_ready_count = sum(1 for row in rows if row["layoutlm_ready"])
    quality_report_file_count = sum(
        1 for row in rows if row.get("quality_report_present")
    )
    quality_report_ready_file_count = sum(
        1 for row in rows if row.get("quality_report_ready") is True
    )
    standalone_count = sum(
        count
        for kind, count in kind_counts.items()
        if kind.startswith("standalone") or kind == "visual_receipt_summary"
    )
    recommendations: list[str] = []
    if not rows and not errors:
        recommendations.append("no_json_artifacts_found")
    if preflightable_count:
        recommendations.append("run_bundle_on_pattern_artifacts")
    if layoutlm_ready_count:
        recommendations.append("use_training_bundle_for_layoutlm")
    if quality_report_file_count:
        recommendations.append("review_synthesis_quality_report")
    elif layoutlm_ready_count:
        recommendations.append("regenerate_bundle_with_quality_report")
    if standalone_count and not preflightable_count and not layoutlm_ready_count:
        recommendations.append("regenerate_pattern_artifacts_from_receipt_data")
    if kind_counts.get("visual_receipt_summary"):
        recommendations.append("visual_summaries_are_not_training_bundles")

    return {
        "ready": bool(preflightable_count or layoutlm_ready_count),
        "file_count": len(rows),
        "error_count": len(errors),
        "kind_counts": kind_counts,
        "preflightable_file_count": preflightable_count,
        "layoutlm_ready_file_count": layoutlm_ready_count,
        "quality_report_file_count": quality_report_file_count,
        "quality_report_ready_file_count": quality_report_ready_file_count,
        "candidate_count": sum(row["candidate_count"] for row in rows),
        "merchant_count": len(
            {
                row["merchant_name"]
                for row in rows
                if row.get("merchant_name")
                and row["merchant_name"] != "Unknown merchant"
            }
        ),
        "recommendations": recommendations,
        "files": rows[:100],
        "errors": errors[:25],
    }


def build_local_synthetic_training_bundle(
    artifacts: list[dict[str, Any]],
    *,
    min_ready_share: float = 0.7,
    min_avg_readiness_score: float = 0.7,
    min_grounded_candidate_share: float = 0.5,
    min_structure_similarity: float = 0.6,
    max_per_merchant: int = 5,
    max_per_merchant_operation: int = 2,
    require_high_fidelity: bool = False,
) -> dict[str, Any]:
    """Build a local LayoutLM synthetic training bundle from pattern artifacts."""
    from receipt_layoutlm.data_loader import (
        _candidate_rows,
        _merchant_synthesis_contracts_by_merchant,
        _select_synthetic_training_examples,
        _synthetic_candidate_quality_failure,
        _synthetic_structure_component_thresholds,
    )

    structure_component_thresholds = _synthetic_structure_component_thresholds()
    preflight = summarize_local_synthesis_preflight(
        artifacts,
        min_ready_share=min_ready_share,
        min_avg_readiness_score=min_avg_readiness_score,
        min_grounded_candidate_share=min_grounded_candidate_share,
    )
    rows = _with_derived_candidate_quality(
        _candidate_rows(artifacts),
        min_structure_similarity=min_structure_similarity,
        structure_component_thresholds=structure_component_thresholds,
        quality_failure_fn=_synthetic_candidate_quality_failure,
    )
    preliminary_contracts = build_merchant_synthesis_contracts(
        artifacts,
        min_structure_similarity=min_structure_similarity,
        structure_component_thresholds=structure_component_thresholds,
        max_per_merchant=max_per_merchant,
        max_per_merchant_operation=max_per_merchant_operation,
    )
    merchant_contracts = _merchant_synthesis_contracts_by_merchant(
        {"merchant_synthesis_contracts": preliminary_contracts}
    )
    selection = _select_synthetic_training_examples(
        rows,
        max_per_merchant=max_per_merchant,
        max_per_merchant_operation=max_per_merchant_operation,
        min_structure_similarity=min_structure_similarity,
        merchant_contracts=merchant_contracts,
        # A curated, ready-to-train bundle requires every accepted candidate to
        # be high-fidelity (rejecting the rest rather than holding the whole
        # batch). Off by default so the lower-level builder and ordinary
        # training keep the looser structure threshold.
        require_high_fidelity=require_high_fidelity,
    )
    reasons = list(preflight["reasons"])
    if not selection.candidates_accepted:
        reasons.append("no_layoutlm_accepted_candidates")

    selected_rows = selection.accepted_rows
    candidate_mix = summarize_bundle_candidate_mix(
        rows,
        selected_rows,
        selection.rejected_rows,
    )
    if balance_failure := _synthetic_mix_balance_failure(
        preflight=preflight,
        candidate_mix=candidate_mix,
    ):
        reasons.append(balance_failure)
    merchant_synthesis_contracts = build_merchant_synthesis_contracts(
        artifacts,
        candidate_mix=candidate_mix,
        min_structure_similarity=min_structure_similarity,
        structure_component_thresholds=structure_component_thresholds,
        max_per_merchant=max_per_merchant,
        max_per_merchant_operation=max_per_merchant_operation,
    )
    training_batch_policy = _synthetic_training_batch_policy(
        candidate_mix=candidate_mix,
        selected_rows=selected_rows,
        max_per_merchant=max_per_merchant,
        max_per_merchant_operation=max_per_merchant_operation,
        bundle_reasons=reasons,
    )
    policy_bundle_blockers = {
        "accepted_selected_count_mismatch",
        "missing_candidate_quality_assessment",
        "no_high_fidelity_candidate_quality",
    }
    for reason in training_batch_policy.get("hold_reasons") or []:
        if reason in policy_bundle_blockers and reason not in reasons:
            reasons.append(reason)
    selection_summary = {
        "candidates_seen": selection.candidates_seen,
        "candidates_accepted": selection.candidates_accepted,
        "candidates_rejected": selection.candidates_rejected,
        "rejection_reasons": selection.rejection_reasons,
        "rejected_candidate_examples": selection.rejected_rows[:50],
        "contract_gate": {
            "enabled": True,
            "merchant_contract_count": len(preliminary_contracts),
            "ready_merchant_contract_count": sum(
                1
                for contract in preliminary_contracts
                if str(contract.get("status") or "").strip().lower() == "ready"
            ),
        },
        "max_per_merchant": max_per_merchant,
        "max_per_merchant_operation": max_per_merchant_operation,
        "min_structure_similarity": min_structure_similarity,
        "structure_component_thresholds": structure_component_thresholds,
        "accepted_candidate_ids": [
            str(row.get("candidate_id") or row.get("receipt_key") or "")
            for row in selected_rows[:50]
        ],
        "accepted_candidate_examples": [
            _compact_selection_candidate(row, rank=rank)
            for rank, row in enumerate(selected_rows[:10], start=1)
        ],
        "training_batch_policy": training_batch_policy,
    }
    bundle = {
        "schema_version": "layoutlm-synthetic-training-bundle-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ready": not reasons,
        "reasons": reasons,
        "validation_policy": "real_receipts_only",
        "source_artifact_count": len(artifacts),
        "source_receipt_quality": summarize_source_receipt_quality(artifacts),
        "preflight": preflight,
        "selection": selection_summary,
        "candidate_mix": candidate_mix,
        "merchant_synthesis_contracts": merchant_synthesis_contracts,
        "synthetic_training_batch_policy": training_batch_policy,
        "synthetic_training_examples": selected_rows,
    }
    bundle["synthesis_quality_report"] = build_local_synthesis_quality_report(
        bundle,
        artifacts=artifacts,
    )
    return bundle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify deployed synthetic LayoutLM replay and audit flow"
    )
    parser.add_argument("--env", default="dev", help="Pulumi stack name")
    parser.add_argument("--region", default="us-east-1")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        help="Check whether deployed replay/audit resources are ready",
    )

    inventory = subparsers.add_parser(
        "inventory",
        help="Classify local JSON artifacts without touching AWS",
    )
    inventory.add_argument("--artifact-file", action="append", default=[])
    inventory.add_argument("--artifact-dir", action="append", default=[])

    build_artifacts = subparsers.add_parser(
        "build-artifacts",
        help="Build offline pattern artifacts from local receipt-structure JSON",
    )
    build_artifacts.add_argument("--receipt-file", action="append", default=[])
    build_artifacts.add_argument("--receipt-dir", action="append", default=[])
    build_artifacts.add_argument("--output-dir", required=True)
    build_artifacts.add_argument("--max-candidates", type=int, default=25)

    local_pipeline = subparsers.add_parser(
        "local-pipeline",
        help="Build artifacts and a curated bundle from local receipt JSON",
    )
    local_pipeline.add_argument("--receipt-file", action="append", default=[])
    local_pipeline.add_argument("--receipt-dir", action="append", default=[])
    local_pipeline.add_argument("--artifact-output-dir", required=True)
    local_pipeline.add_argument("--bundle-output", required=True)
    local_pipeline.add_argument("--max-candidates", type=int, default=25)
    local_pipeline.add_argument("--min-ready-share", type=float, default=0.7)
    local_pipeline.add_argument("--min-avg-readiness-score", type=float, default=0.7)
    local_pipeline.add_argument(
        "--min-grounded-candidate-share", type=float, default=0.5
    )
    local_pipeline.add_argument("--min-structure-similarity", type=float, default=0.6)
    local_pipeline.add_argument("--max-per-merchant", type=int, default=20)
    local_pipeline.add_argument("--max-per-merchant-operation", type=int, default=8)

    preflight = subparsers.add_parser(
        "preflight",
        help="Offline readiness gate for local pattern artifact JSON files",
    )
    preflight.add_argument("--artifact-file", action="append", default=[])
    preflight.add_argument("--artifact-dir", action="append", default=[])
    preflight.add_argument("--min-ready-share", type=float, default=0.7)
    preflight.add_argument("--min-avg-readiness-score", type=float, default=0.7)
    preflight.add_argument("--min-grounded-candidate-share", type=float, default=0.5)

    bundle = subparsers.add_parser(
        "bundle",
        help="Build an offline curated LayoutLM synthetic training bundle",
    )
    bundle.add_argument("--artifact-file", action="append", default=[])
    bundle.add_argument("--artifact-dir", action="append", default=[])
    bundle.add_argument("--output", required=True)
    bundle.add_argument("--min-ready-share", type=float, default=0.7)
    bundle.add_argument("--min-avg-readiness-score", type=float, default=0.7)
    bundle.add_argument("--min-grounded-candidate-share", type=float, default=0.5)
    bundle.add_argument("--min-structure-similarity", type=float, default=0.6)
    bundle.add_argument("--max-per-merchant", type=int, default=20)
    bundle.add_argument("--max-per-merchant-operation", type=int, default=8)

    report = subparsers.add_parser(
        "report",
        help="Summarize a local synthetic training bundle by merchant and evidence",
    )
    report.add_argument("--bundle-file", required=True)
    report.add_argument("--artifact-file", action="append", default=[])
    report.add_argument("--artifact-dir", action="append", default=[])

    start = subparsers.add_parser(
        "start",
        help="Start a synthetic replay Step Function execution",
    )
    start.add_argument("--baseline-job-ref", required=True)
    start.add_argument("--confirm-cost-ack", action="store_true")
    start.add_argument("--limit", type=int, required=True)
    start.add_argument("--since-date")
    start.add_argument("--langchain-project")
    start.add_argument("--run-analytics", action="store_true")
    start.add_argument("--hyperparameters")
    start.add_argument("--epochs", type=int, default=1)
    start.add_argument("--early-stopping-patience", type=int, default=1)
    start.add_argument("--instance-type", default="ml.g5.xlarge")
    start.add_argument("--instance-count", type=int, default=1)
    start.add_argument("--use-spot", dest="use_spot", action="store_true", default=True)
    start.add_argument("--no-spot", dest="use_spot", action="store_false")
    start.add_argument("--max-runtime-hours", type=int, default=1)
    start.add_argument(
        "--max-aws-spend-usd",
        type=float,
        default=_default_experiment_max_aws_spend_usd(),
        help=(
            "Abort before launch when current month-to-date AWS spend is at "
            "or above this cap. Set <=0 to disable."
        ),
    )
    start.add_argument("--name-prefix", default="synthetic-replay")
    start.add_argument("--skip-preflight", action="store_true")
    start.add_argument("--wait", action="store_true")
    start.add_argument("--wait-seconds", type=int, default=7200)
    start.add_argument("--poll-seconds", type=int, default=30)
    start.add_argument("--wait-audit", action="store_true")
    start.add_argument("--audit-wait-seconds", type=int, default=7200)

    audit = subparsers.add_parser(
        "audit",
        help="Fetch or wait for the S3 audit JSON for a training job",
    )
    audit.add_argument("--training-job-name", required=True)
    audit.add_argument("--wait", action="store_true")
    audit.add_argument("--wait-seconds", type=int, default=7200)
    audit.add_argument("--poll-seconds", type=int, default=30)

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "inventory":
        inventory = inventory_local_artifacts(
            artifact_files=args.artifact_file,
            artifact_dirs=args.artifact_dir,
        )
        json_print(inventory)
        return 0 if inventory["ready"] else 2

    if args.command == "build-artifacts":
        receipt_groups = load_local_receipt_groups(
            receipt_files=args.receipt_file,
            receipt_dirs=args.receipt_dir,
        )
        artifacts = build_local_pattern_artifacts_from_receipts(
            receipt_groups,
            max_candidates=args.max_candidates,
        )
        output_files = write_local_pattern_artifacts(
            artifacts,
            output_dir=args.output_dir,
        )
        json_print(
            summarize_built_pattern_artifacts(
                artifacts,
                output_files=output_files,
            )
        )
        return 0 if artifacts else 2

    if args.command == "local-pipeline":
        result = run_local_synthetic_pipeline(
            receipt_files=args.receipt_file,
            receipt_dirs=args.receipt_dir,
            artifact_output_dir=args.artifact_output_dir,
            bundle_output=args.bundle_output,
            max_candidates=args.max_candidates,
            min_ready_share=args.min_ready_share,
            min_avg_readiness_score=args.min_avg_readiness_score,
            min_grounded_candidate_share=args.min_grounded_candidate_share,
            min_structure_similarity=args.min_structure_similarity,
            max_per_merchant=args.max_per_merchant,
            max_per_merchant_operation=args.max_per_merchant_operation,
        )
        json_print(result)
        return 0 if result["ready"] else 2

    if args.command == "preflight":
        artifacts = load_local_pattern_artifacts(
            artifact_files=args.artifact_file,
            artifact_dirs=args.artifact_dir,
        )
        summary = summarize_local_synthesis_preflight(
            artifacts,
            min_ready_share=args.min_ready_share,
            min_avg_readiness_score=args.min_avg_readiness_score,
            min_grounded_candidate_share=args.min_grounded_candidate_share,
        )
        json_print(summary)
        return 0 if summary["ready"] else 2

    if args.command == "bundle":
        artifacts = load_local_pattern_artifacts(
            artifact_files=args.artifact_file,
            artifact_dirs=args.artifact_dir,
        )
        bundle = build_local_synthetic_training_bundle(
            artifacts,
            min_ready_share=args.min_ready_share,
            min_avg_readiness_score=args.min_avg_readiness_score,
            min_grounded_candidate_share=args.min_grounded_candidate_share,
            min_structure_similarity=args.min_structure_similarity,
            max_per_merchant=args.max_per_merchant,
            max_per_merchant_operation=args.max_per_merchant_operation,
            require_high_fidelity=True,
        )
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(bundle, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        candidate_mix_output = {
            "merchant_count": bundle["candidate_mix"]["merchant_count"],
            "accepted_merchant_count": bundle["candidate_mix"][
                "accepted_merchant_count"
            ],
            "rejected_count": bundle["candidate_mix"]["rejected_count"],
            "rejection_reasons": bundle["candidate_mix"]["rejection_reasons"],
            "accepted_operation_counts": bundle["candidate_mix"][
                "accepted_operation_counts"
            ],
            "accepted_category_counts": bundle["candidate_mix"][
                "accepted_category_counts"
            ],
            "accepted_field_replacement_counts": bundle["candidate_mix"][
                "accepted_field_replacement_counts"
            ],
            "accepted_structure_similarity": bundle["candidate_mix"][
                "accepted_structure_similarity"
            ],
            "accepted_structure_components": bundle["candidate_mix"].get(
                "accepted_structure_components"
            )
            or {},
        }
        if _safe_int(
            (
                bundle["candidate_mix"].get("accepted_real_baseline_comparison") or {}
            ).get("count")
        ):
            candidate_mix_output["accepted_real_baseline_comparison"] = bundle[
                "candidate_mix"
            ]["accepted_real_baseline_comparison"]
        if bundle["candidate_mix"].get("accepted_candidate_quality"):
            candidate_mix_output["accepted_candidate_quality"] = bundle[
                "candidate_mix"
            ]["accepted_candidate_quality"]
        if bundle["candidate_mix"].get("accepted_candidate_quality_components"):
            candidate_mix_output["accepted_candidate_quality_components"] = bundle[
                "candidate_mix"
            ]["accepted_candidate_quality_components"]
        if bundle["candidate_mix"].get("accepted_source_lineage"):
            candidate_mix_output["accepted_source_lineage"] = bundle["candidate_mix"][
                "accepted_source_lineage"
            ]
        json_print(
            {
                "ready": bundle["ready"],
                "reasons": bundle["reasons"],
                "output": str(output_path),
                "source_artifact_count": bundle["source_artifact_count"],
                "preflight": {
                    "merchant_count": bundle["preflight"]["merchant_count"],
                    "ready_merchant_count": bundle["preflight"]["ready_merchant_count"],
                    "avg_readiness_score": bundle["preflight"]["avg_readiness_score"],
                    "llm_execution": bundle["preflight"].get("llm_execution") or {},
                },
                "selection": bundle["selection"],
                "merchant_synthesis_contract_count": len(
                    bundle.get("merchant_synthesis_contracts") or []
                ),
                "candidate_mix": candidate_mix_output,
            }
        )
        return 0 if bundle["ready"] else 2

    if args.command == "report":
        with Path(args.bundle_file).open("r", encoding="utf-8") as handle:
            bundle = json.load(handle)
        if not isinstance(bundle, dict):
            raise ValueError(f"Bundle must be a JSON object: {args.bundle_file}")
        artifacts = load_local_pattern_artifacts(
            artifact_files=args.artifact_file,
            artifact_dirs=args.artifact_dir,
        )
        embedded_report = (
            bundle.get("synthesis_quality_report")
            or bundle.get("quality_report")
            or bundle.get("report")
        )
        report = (
            embedded_report
            if isinstance(embedded_report, dict) and embedded_report and not artifacts
            else build_local_synthesis_quality_report(
                bundle,
                artifacts=artifacts,
            )
        )
        json_print(report)
        return 0 if report.get("training_ready", report["ready"]) else 2

    outputs = load_outputs(args.env)
    status = describe_deployment(outputs, env=args.env, region=args.region)

    if args.command == "status":
        json_print(status)
        return 0 if status["ready"] else 2

    if args.command == "start":
        if not args.skip_preflight:
            require_ready(status)
        validate_start_args(args)
        budget = require_experiment_budget_remaining(
            region=args.region,
            max_aws_spend_usd=args.max_aws_spend_usd,
        )
        payload = build_execution_input(args)
        launch = start_execution(
            outputs,
            region=args.region,
            payload=payload,
            name_prefix=args.name_prefix,
        )
        result: dict[str, Any] = {
            "deployment": status,
            "budget": budget,
            "launch": launch,
        }
        if args.wait:
            execution = wait_for_execution(
                launch["execution_arn"],
                region=args.region,
                poll_seconds=args.poll_seconds,
                timeout_seconds=args.wait_seconds,
            )
            result["execution"] = execution
            job_name = extract_training_job_name(execution.get("output", {}))
            result["training_job_name"] = job_name
            if args.wait_audit and job_name:
                result["audit"] = wait_for_audit_result(
                    bucket=status["batch_bucket"],
                    job_name=job_name,
                    region=args.region,
                    poll_seconds=args.poll_seconds,
                    timeout_seconds=args.audit_wait_seconds,
                )
        json_print(result)
        return 0

    if args.command == "audit":
        bucket = status.get("batch_bucket")
        if not bucket:
            raise RuntimeError("Missing label_evaluator_batch_bucket_name output")
        if args.wait:
            audit_result = wait_for_audit_result(
                bucket=bucket,
                job_name=args.training_job_name,
                region=args.region,
                poll_seconds=args.poll_seconds,
                timeout_seconds=args.wait_seconds,
            )
        else:
            audit_result = fetch_audit_result(
                bucket=bucket,
                job_name=args.training_job_name,
                region=args.region,
            )
        json_print(
            {
                "training_job_name": args.training_job_name,
                "audit": audit_result,
                "audit_found": audit_result is not None,
            }
        )
        return 0 if audit_result is not None else 2

    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
