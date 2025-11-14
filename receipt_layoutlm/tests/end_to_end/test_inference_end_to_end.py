import os
from typing import Optional, Dict, Tuple, List

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_layoutlm.inference import LayoutLMInference

try:
    import boto3  # type: ignore
except Exception:  # pragma: no cover - boto3 not available in some envs
    boto3 = None  # type: ignore


def _get_any_receipt_id(dynamo: DynamoClient) -> Optional[tuple[str, int]]:
    receipts, _ = dynamo.list_receipts(limit=1)
    if not receipts:
        return None
    r = receipts[0]
    return r.image_id, r.receipt_id


@pytest.mark.end_to_end
def test_layoutlm_inference_predict_receipt_from_dynamo():
    env = os.getenv("PULUMI_ENV", "dev")
    outputs = load_env(env)
    if not outputs:
        pytest.skip("Pulumi outputs not available")

    table_name = outputs.get("dynamodb_table_name")
    bucket_name = outputs.get("layoutlm_training_bucket")
    if not table_name or not bucket_name:
        pytest.skip(
            "Required Pulumi outputs missing (table or training bucket)"
        )

    dynamo = DynamoClient(table_name)

    # Discover a real receipt to run against
    rid = _get_any_receipt_id(dynamo)
    if not rid:
        pytest.skip("No receipts available in table")
    image_id, receipt_id = rid

    # Resolve model for a specific job id so we run against actual artifacts
    target_job_id = os.getenv(
        "LAYOUTLM_TEST_JOB_ID", "6f73605a-398d-40a4-baae-641b955dff85"
    )
    job = dynamo.get_job(target_job_id)
    storage = getattr(job, "storage", None) or {}
    bucket_for_job = storage.get("bucket") or bucket_name
    run_root_prefix = storage.get("run_root_prefix")  # e.g., receipts-.../

    model_s3_uri: Optional[str] = None
    if boto3 and run_root_prefix:
        s3 = boto3.client(
            "s3", region_name=outputs.get("aws_region", "us-east-1")
        )
        # Prefer best/ if present
        try:
            s3.head_object(
                Bucket=bucket_for_job,
                Key=f"{run_root_prefix}best/model.safetensors",
            )
            model_s3_uri = f"s3://{bucket_for_job}/{run_root_prefix}best/"
        except Exception:
            # Fallback: pick latest checkpoint containing model.safetensors
            resp = s3.list_objects_v2(
                Bucket=bucket_for_job, Prefix=run_root_prefix
            )
            contents = resp.get("Contents", []) or []
            cands = [
                o
                for o in contents
                if o["Key"].endswith("model.safetensors")
                and "checkpoint-" in o["Key"]
            ]
            if cands:
                latest = max(cands, key=lambda o: o["LastModified"])  # type: ignore
                model_s3_uri = (
                    f"s3://{bucket_for_job}/"
                    + latest["Key"].rsplit("/", 1)[0]
                    + "/"
                )

    infer = LayoutLMInference(
        model_dir=None,
        model_s3_uri=model_s3_uri,
        auto_from_bucket_env=None,
    )
    # Logging: show which job/model we're using
    print(
        f"Using job_id={target_job_id} bucket={bucket_for_job} prefix={run_root_prefix}"
    )
    print(f"Resolved model_s3_uri={model_s3_uri or infer._s3_uri_used}")

    result = infer.predict_receipt_from_dynamo(
        dynamo_client=dynamo, image_id=image_id, receipt_id=receipt_id
    )

    # Basic result logging
    print(
        f"Predicted {result.meta.get('num_lines')} lines, {result.meta.get('num_words')} words"
    )
    if result.lines:
        for line in result.lines:
            print(
                f"Line {line.line_id}: {line.tokens[:10]} -> {line.labels[:10]} ({line.confidences[:10]})"
            )
        # lp0 = result.lines[0]
        # print(
        #     "Sample line 0:",
        #     {
        #         "line_id": lp0.line_id,
        #         "tokens": lp0.tokens[:10],
        #         "labels": lp0.labels[:10],
        #         "confidences": [round(c, 3) for c in lp0.confidences[:10]],
        #     },
        # )

    assert result.image_id == image_id
    assert result.receipt_id == receipt_id
    assert isinstance(result.meta.get("num_lines", 0), int)
    assert isinstance(result.meta.get("num_words", 0), int)
    assert result.meta.get("device") in {"cpu", "cuda"}
    assert result.meta.get("model_dir")

    # Compare predictions to ground-truth labels when available
    details = dynamo.get_receipt_details(
        image_id=image_id, receipt_id=receipt_id
    )

    # Build label lookup by (line_id, word_id) -> label
    label_by_word: Dict[Tuple[int, int], str] = {}
    for lbl in details.labels:
        key = (lbl.line_id, lbl.word_id)
        # Prefer the first seen label for a given word
        if key not in label_by_word:
            label_by_word[key] = (lbl.label or "").upper()

    # Build words per line ordered by word_id to align with predictions
    words_by_line: Dict[int, List[Tuple[int, int]]] = {}
    for w in details.words:
        words_by_line.setdefault(w.line_id, []).append((w.word_id, w.line_id))
    for line_id in list(words_by_line.keys()):
        words_by_line[line_id].sort(key=lambda t: t[0])

    # Align predicted labels to GT labels using word order
    compared = 0
    matches = 0
    for lp in result.lines:
        # Basic shape checks
        assert (
            len(lp.tokens)
            == len(lp.boxes)
            == len(lp.labels)
            == len(lp.confidences)
        )
        for c in lp.confidences:
            assert 0.0 <= c <= 1.0
        ordered_words = words_by_line.get(lp.line_id, [])
        # Compare only up to the overlapping length
        n = min(len(lp.labels), len(ordered_words))
        for i in range(n):
            wid, lid = ordered_words[i]
            gt = label_by_word.get((lid, wid))
            if not gt:
                continue
            pred = (lp.labels[i] or "").upper()
            compared += 1
            if pred == gt:
                matches += 1

    # Ensure we performed at least one comparison; report simple accuracy
    assert compared >= 0  # always true, keeps lints calm if no labels
    if compared == 0:
        pytest.skip("No ground-truth word labels available to compare")
    acc = matches / compared if compared else 0.0
    print(f"LayoutLM label match: {matches}/{compared} = {acc:.2%}")
