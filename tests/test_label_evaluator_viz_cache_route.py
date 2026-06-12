import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("S3_CACHE_BUCKET", "test-cache-bucket")

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra/routes/label_evaluator_viz_cache/lambdas/index.py"
)
spec = importlib.util.spec_from_file_location(
    "label_evaluator_viz_cache_lambda",
    MODULE_PATH,
)
assert spec is not None
assert spec.loader is not None
viz_cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viz_cache)


def test_receipt_health_issues_execution_query_honors_limit_and_filters(
    monkeypatch,
):
    run_payload = {
        "execution_id": "exec-123",
        "cached_at": "2026-06-12T22:41:50+00:00",
        "summary": {
            "total_issues": 3,
            "by_issue_type": {"GRAND_TOTAL": 2, "SUBTOTAL": 1},
        },
        "issues": [
            {
                "issue_id": "issue-a",
                "observed_at": "2026-06-12T22:41:50+00:00",
                "image_id": "image-a",
                "check_id": "financial_math",
                "merchant_name": "Beta",
            },
            {
                "issue_id": "issue-b",
                "observed_at": "2026-06-12T22:41:50+00:00",
                "image_id": "image-a",
                "check_id": "financial_math",
                "merchant_name": "Alpha",
            },
            {
                "issue_id": "issue-c",
                "observed_at": "2026-06-12T22:41:50+00:00",
                "image_id": "image-b",
                "check_id": "merchant_identity",
                "merchant_name": "Gamma",
            },
        ],
    }

    def fake_fetch_json_key(key):
        assert key == "receipt-health/runs/exec-123/issues.json"
        return run_payload

    monkeypatch.setattr(viz_cache, "_fetch_json_key", fake_fetch_json_key)

    response = viz_cache._handle_receipt_health_issues_get(
        {
            "execution_id": "exec-123",
            "check_id": "financial_math",
            "image_id": "image-a",
            "limit": "1",
        }
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["execution_id"] == "exec-123"
    assert body["cached_at"] == "2026-06-12T22:41:50+00:00"
    assert body["count"] == 1
    assert body["limit"] == 1
    assert body["state"] == "all"
    assert body["summary"] == run_payload["summary"]
    assert body["issues"] == [run_payload["issues"][1]]
