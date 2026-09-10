"""Verify the OCR source mapping consumes per-message failure responses."""

import ast
from pathlib import Path


def test_ocr_results_mapping_enables_partial_batch_redrive() -> None:
    """The mapping must honor batchItemFailures emitted by the handler."""
    source = (
        Path(__file__).parents[1] / "upload_images" / "infra.py"
    ).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "EventSourceMapping"
        and node.args
        and "ocr-results-mapping" in ast.unparse(node.args[0])
    ]
    assert len(calls) == 1
    values = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert ast.literal_eval(values["function_response_types"]) == [
        "ReportBatchItemFailures"
    ]
    assert ast.literal_eval(values["enabled"]) is True
