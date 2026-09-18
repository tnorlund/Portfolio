"""Verify the OCR source mapping consumes per-message failure responses."""

import ast
from pathlib import Path

_INFRA = Path(__file__).parents[1] / "upload_images" / "infra.py"
_ROUTING_DAL = (
    Path(__file__).parents[2]
    / "receipt_dynamo"
    / "receipt_dynamo"
    / "data"
    / "_ocr_routing_decision.py"
)


def _keywords(source: str, attr: str, name_fragment: str) -> dict:
    """Keyword arguments of the single ``attr(...)`` call naming a resource."""
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == attr)
            or (isinstance(node.func, ast.Name) and node.func.id == attr)
        )
        and node.args
        and name_fragment in ast.unparse(node.args[0])
    ]
    assert len(calls) == 1
    return {keyword.arg: keyword.value for keyword in calls[0].keywords}


def _default_lease_seconds() -> int:
    """Default ``lease_seconds`` of claim_ocr_routing_decision."""
    tree = ast.parse(_ROUTING_DAL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "claim_ocr_routing_decision"
        ):
            defaults = dict(
                zip(
                    [arg.arg for arg in node.args.kwonlyargs],
                    node.args.kw_defaults,
                )
            )
            return ast.literal_eval(defaults["lease_seconds"])
    raise AssertionError("claim_ocr_routing_decision not found")


def test_ocr_results_mapping_enables_partial_batch_redrive() -> None:
    """The mapping must honor batchItemFailures emitted by the handler."""
    source = _INFRA.read_text(encoding="utf-8")
    values = _keywords(source, "EventSourceMapping", "ocr-results-mapping")
    assert ast.literal_eval(values["function_response_types"]) == [
        "ReportBatchItemFailures"
    ]
    assert ast.literal_eval(values["enabled"]) is True


def test_ocr_results_visibility_covers_routing_lease() -> None:
    """A redelivery must not arrive while a dead attempt's lease is held.

    The lease outlives the 900 s Lambda timeout so a crashed invocation
    cannot overlap its successor. If SQS redelivers before that lease
    expires, the claim is rejected as busy and a receive is wasted.
    """
    source = _INFRA.read_text(encoding="utf-8")
    values = _keywords(source, "Queue", "ocr-results-queue")
    visibility = ast.literal_eval(values["visibility_timeout_seconds"])
    assert visibility >= _default_lease_seconds()
    assert visibility >= 900
