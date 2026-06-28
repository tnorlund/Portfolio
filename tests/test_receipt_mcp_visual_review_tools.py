"""Regression tests for synthetic visual review MCP dispatch."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import types


MCP_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "receipt_mcp_server.py"
)


def _load_mcp_server_module(monkeypatch):
    class FakeServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_tools(self):
            return lambda func: func

        def call_tool(self):
            return lambda func: func

    class FakeContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")
    stdio_module = types.ModuleType("mcp.server.stdio")
    types_module = types.ModuleType("mcp.types")
    server_module.Server = FakeServer
    stdio_module.stdio_server = object
    types_module.TextContent = FakeContent
    types_module.Tool = FakeContent

    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.stdio", stdio_module)
    monkeypatch.setitem(sys.modules, "mcp.types", types_module)

    spec = importlib.util.spec_from_file_location(
        "receipt_mcp_server_for_test",
        MCP_SERVER_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_visual_review_target_listing_loads_dynamo_state():
    source = MCP_SERVER_PATH.read_text()

    list_branch = source.split(
        'if name == "list_synthetic_receipt_visual_review_targets":', 1
    )[1].split('if name == "record_synthetic_receipt_visual_review":', 1)[0]

    assert "dynamo_client = get_dynamo_client()" in list_branch
    assert "list_synthetic_receipt_visual_review_targets_impl(\n" in list_branch
    assert "dynamo_client,\n" in list_branch
    assert "review_state_error" in list_branch


def test_visual_review_target_listing_includes_base_receipt_image(monkeypatch):
    module = _load_mcp_server_module(monkeypatch)

    class FakeReceipt:
        def __init__(self, image_id: str, receipt_id: int):
            stem = f"assets/{image_id}_RECEIPT_{receipt_id:05d}"
            self.cdn_s3_key = f"{stem}.jpg"
            self.cdn_webp_s3_key = f"{stem}.webp"
            self.cdn_avif_s3_key = None
            self.cdn_thumbnail_s3_key = f"{stem}_thumbnail.jpg"
            self.cdn_small_s3_key = f"{stem}_small.jpg"
            self.cdn_medium_s3_key = f"{stem}_medium.jpg"

    class FakeDetails:
        def __init__(self, image_id: str, receipt_id: int):
            self.receipt = FakeReceipt(image_id, receipt_id)

    class FakeDynamoClient:
        def list_synthetic_receipt_visual_reviews_for_job(self, *_args, **_kwargs):
            return [], None

        def get_receipt_details(self, image_id: str, receipt_id: int):
            return FakeDetails(image_id, receipt_id)

    result = asyncio.run(
        module.list_synthetic_receipt_visual_review_targets_impl(
            FakeDynamoClient(),
            job_id=module.DEFAULT_SYNTHETIC_REVIEW_JOB_ID,
        )
    )

    assert result["target_count"] == 3
    target = result["targets"][0]
    base_image = target["base_receipt_image"]
    assert base_image == {
        "image_id": "37333eb8-0025-4711-98ae-7b72fd83abd5",
        "receipt_id": 1,
        "lookup_status": "available",
        "url": (
            "https://dev.tylernorlund.com/assets/"
            "37333eb8-0025-4711-98ae-7b72fd83abd5_RECEIPT_00001.jpg"
        ),
        "variants": {
            "webp": (
                "https://dev.tylernorlund.com/assets/"
                "37333eb8-0025-4711-98ae-7b72fd83abd5_RECEIPT_00001.webp"
            ),
            "thumbnail": (
                "https://dev.tylernorlund.com/assets/"
                "37333eb8-0025-4711-98ae-7b72fd83abd5_RECEIPT_00001_thumbnail.jpg"
            ),
            "small": (
                "https://dev.tylernorlund.com/assets/"
                "37333eb8-0025-4711-98ae-7b72fd83abd5_RECEIPT_00001_small.jpg"
            ),
            "medium": (
                "https://dev.tylernorlund.com/assets/"
                "37333eb8-0025-4711-98ae-7b72fd83abd5_RECEIPT_00001_medium.jpg"
            ),
        },
    }


def test_base_receipt_image_reference_can_degrade_without_dynamo(monkeypatch):
    module = _load_mcp_server_module(monkeypatch)

    result = asyncio.run(
        module._base_receipt_image_reference(
            None,
            "00ded398-af6f-4a49-86f7-c79ccb554e48#00002",
        )
    )

    assert result == {
        "image_id": "00ded398-af6f-4a49-86f7-c79ccb554e48",
        "receipt_id": 2,
        "lookup_status": "not_loaded",
    }


def test_visual_review_summary_uses_latest_open_recommendations(monkeypatch):
    module = _load_mcp_server_module(monkeypatch)
    common = {
        "job_id": module.DEFAULT_SYNTHETIC_REVIEW_JOB_ID,
        "synthetic_image_id": "sprouts-add-line-item",
        "reviewer": "claude-mcp",
        "reviewer_model": "claude-sonnet",
        "merchant_name": "Sprouts Farmers Market",
        "operation": "add_line_item",
        "fidelity_score": None,
        "alignment_score": None,
        "issue_count": 0,
        "findings": [],
        "rubric": {},
        "metadata": {},
    }
    reviews = [
        SimpleNamespace(
            **common,
            review_id="r1",
            candidate_id="candidate-a",
            status="needs_iteration",
            created_at="2026-06-27T12:00:00+00:00",
            realism_score=0.5,
            recommendations=["old fixed recommendation"],
        ),
        SimpleNamespace(
            **common,
            review_id="r2",
            candidate_id="candidate-a",
            status="accepted",
            created_at="2026-06-27T13:00:00+00:00",
            realism_score=0.9,
            recommendations=[],
        ),
        SimpleNamespace(
            **common,
            review_id="r3",
            candidate_id="candidate-b",
            status="needs_iteration",
            created_at="2026-06-27T14:00:00+00:00",
            realism_score=0.7,
            recommendations=["current footer action"],
        ),
    ]

    summary = module._summarize_visual_review_rows(reviews)

    assert summary["review_count"] == 3
    assert summary["reviewed_candidate_count"] == 2
    assert summary["status_counts"] == {"needs_iteration": 2, "accepted": 1}
    assert summary["avg_scores"]["realism_score"] == 0.7
    assert summary["latest_reviews"][0]["review_id"] == "r3"
    assert summary["open_recommendations"] == ["current footer action"]
