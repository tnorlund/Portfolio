"""Regression tests for synthetic visual review MCP dispatch."""

import asyncio
import importlib.util
import json
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


def test_dynamo_only_smoke_tools_do_not_initialize_chroma(monkeypatch):
    module = _load_mcp_server_module(monkeypatch)

    class FakeDynamoClient:
        def __init__(self):
            self.updated_label = None

        def get_receipt_places_by_merchant(self, **_kwargs):
            return [
                SimpleNamespace(
                    image_id="ed28a4ce-2258-4745-87ba-2fc662c94abf",
                    receipt_id=2,
                )
            ], None

        def get_receipt_word_labels_by_label(self, **_kwargs):
            return [
                SimpleNamespace(
                    text="www.costco.com",
                    validation_status="INVALID",
                    image_id="ed28a4ce-2258-4745-87ba-2fc662c94abf",
                    receipt_id=2,
                    line_id=32,
                    word_id=3,
                    label_proposed_by="test",
                )
            ], None

        def get_receipt_word_label(self, **_kwargs):
            return SimpleNamespace(
                image_id="ed28a4ce-2258-4745-87ba-2fc662c94abf",
                receipt_id=2,
                line_id=32,
                word_id=3,
                label="WEBSITE",
                reasoning="already invalid",
                timestamp_added="2026-06-28T00:00:00+00:00",
                validation_status="INVALID",
                label_consolidated_from=None,
            )

        def update_receipt_word_label(self, label):
            self.updated_label = label

    fake_dynamo = FakeDynamoClient()

    def fail_get_clients():
        raise AssertionError("Dynamo-only smoke tools must not initialize Chroma")

    monkeypatch.setattr(module, "get_clients", fail_get_clients)
    monkeypatch.setattr(module, "get_dynamo_client", lambda: fake_dynamo)

    merchant_response = asyncio.run(
        module.call_tool(
            "get_receipts_by_merchant",
            {"merchant_name": "Costco Wholesale"},
        )
    )
    merchant_payload = json.loads(merchant_response[0].text)
    assert merchant_payload["count"] == 1

    words_response = asyncio.run(
        module.call_tool(
            "list_words_by_label",
            {"label": "WEBSITE", "status_filter": "INVALID", "sample_size": 1000},
        )
    )
    words_payload = json.loads(words_response[0].text)
    assert words_payload["total"] == 1
    assert words_payload["words"][0]["line_id"] == 32

    update_response = asyncio.run(
        module.call_tool(
            "update_word_label",
            {
                "image_id": "ed28a4ce-2258-4745-87ba-2fc662c94abf",
                "receipt_id": 2,
                "line_id": 32,
                "word_id": 3,
                "label": "WEBSITE",
                "new_status": "INVALID",
                "reasoning": "headless MCP smoke test guarded no-op",
            },
        )
    )
    update_payload = json.loads(update_response[0].text)
    assert update_payload["success"] is True
    assert fake_dynamo.updated_label is not None
    assert fake_dynamo.updated_label.validation_status == "INVALID"


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


def test_visual_review_target_listing_can_include_image_metrics(
    monkeypatch,
    tmp_path,
):
    from PIL import Image, ImageDraw

    module = _load_mcp_server_module(monkeypatch)
    synthetic_path = tmp_path / "synthetic.png"
    image = Image.new("RGB", (20, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([5, 6, 14, 23], fill="black")
    image.save(synthetic_path)

    monkeypatch.setattr(
        module,
        "_load_synthetic_receipt_review_targets",
        lambda: [
            {
                "id": "target-a",
                "title": "Target A",
                "merchantName": "Sprouts Farmers Market",
                "operation": "add_line_item",
                "candidateId": "candidate-a",
                "source": "test",
                "imageSrc": "/synthetic-receipts/test.png",
                "local_image_path": str(synthetic_path),
                "local_image_exists": True,
                "baseReceiptKey": "37333eb8-0025-4711-98ae-7b72fd83abd5#00001",
                "reviewFocus": ["compare scale"],
            }
        ],
    )
    monkeypatch.setattr(
        module,
        "_image_visual_metrics_from_url",
        lambda url: {
            "status": "available",
            "source": "url",
            "url": url,
            "width": 10,
            "height": 20,
            "aspect_ratio": 0.5,
            "dark_pixel_density": 0.2,
            "ink_bbox": {
                "left": 1,
                "top": 2,
                "right": 8,
                "bottom": 17,
                "width": 8,
                "height": 16,
            },
            "ink_bbox_area_share": 0.64,
            "margin_shares": {},
        },
    )

    class FakeReceipt:
        cdn_s3_key = "assets/base.jpg"
        cdn_webp_s3_key = None
        cdn_avif_s3_key = None
        cdn_thumbnail_s3_key = None
        cdn_small_s3_key = None
        cdn_medium_s3_key = "assets/base_medium.jpg"

    class FakeDetails:
        receipt = FakeReceipt()

    class FakeDynamoClient:
        def list_synthetic_receipt_visual_reviews_for_job(self, *_args, **_kwargs):
            return [], None

        def get_receipt_details(self, *_args, **_kwargs):
            return FakeDetails()

    result = asyncio.run(
        module.list_synthetic_receipt_visual_review_targets_impl(
            FakeDynamoClient(),
            job_id=module.DEFAULT_SYNTHETIC_REVIEW_JOB_ID,
            include_image_metrics=True,
        )
    )

    metrics = result["targets"][0]["visual_comparison_metrics"]
    assert metrics["synthetic"]["status"] == "available"
    assert metrics["synthetic"]["width"] == 20
    assert metrics["synthetic"]["height"] == 30
    assert metrics["synthetic"]["dark_pixel_density"] > 0
    assert metrics["base_receipt"]["url"].endswith("base_medium.jpg")
    assert metrics["comparison"]["dark_pixel_density_ratio"] is not None
    assert metrics["comparison"]["ink_bbox_height_ratio"] is not None


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
