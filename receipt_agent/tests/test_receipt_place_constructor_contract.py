"""Keep ReceiptPlace builders aligned with the Dynamo entity contract."""

import ast
import inspect
from pathlib import Path

from receipt_dynamo.entities import ReceiptPlace

PLACE_BUILDER_PATHS = (
    Path("receipt_agent/subagents/place_finder/backfill_receipt_place.py"),
    Path(
        "receipt_agent/subagents/place_finder/tools/" "receipt_place_finder.py"
    ),
)


def _receipt_place_calls(path: Path) -> list[tuple[int, set[str]]]:
    """Return line numbers and explicit keywords for ReceiptPlace calls."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ReceiptPlace"
        ):
            continue
        assert all(keyword.arg is not None for keyword in node.keywords)
        calls.append((node.lineno, {keyword.arg for keyword in node.keywords}))
    return calls


def test_receipt_place_builders_use_supported_constructor_arguments() -> None:
    """Catch stale entity arguments in either place-data migration path."""
    project_root = Path(__file__).resolve().parents[1]
    supported = set(inspect.signature(ReceiptPlace).parameters)

    for relative_path in PLACE_BUILDER_PATHS:
        calls = _receipt_place_calls(project_root / relative_path)
        assert calls, f"No ReceiptPlace constructor found in {relative_path}"
        for line_number, arguments in calls:
            assert arguments <= supported, (
                f"Unsupported ReceiptPlace arguments at "
                f"{relative_path}:{line_number}: "
                f"{sorted(arguments - supported)}"
            )
