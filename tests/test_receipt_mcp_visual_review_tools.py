"""Regression tests for synthetic visual review MCP dispatch."""

from pathlib import Path


MCP_SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "receipt_mcp_server.py"
)


def test_visual_review_target_listing_loads_dynamo_state():
    source = MCP_SERVER_PATH.read_text()

    list_branch = source.split(
        'if name == "list_synthetic_receipt_visual_review_targets":', 1
    )[1].split('if name == "record_synthetic_receipt_visual_review":', 1)[0]

    assert "dynamo_client = get_dynamo_client()" in list_branch
    assert "list_synthetic_receipt_visual_review_targets_impl(\n" in list_branch
    assert "dynamo_client,\n" in list_branch
    assert "review_state_error" in list_branch
