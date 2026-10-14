"""Evidence limits and opt-in checks against a real private projection.

Set RECEIPT_LOOKUP_PROJECTION_PATH to an authorized agent/spend.db snapshot
outside the checkout. No receipt fixtures or network access are needed.
Private records must never appear in assertions, reports, or test names.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
HANDLER = REPO / "infra/email_receipt_inbox/lambdas/mcp.py"


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PROJECTION_BUCKET", "unused-offline")
    spec = importlib.util.spec_from_file_location("email_lookup", HANDLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client"):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def real_projection(handler: ModuleType) -> Iterator[sqlite3.Connection]:
    configured = os.environ.get("RECEIPT_LOOKUP_PROJECTION_PATH")
    if not configured:
        pytest.skip("real private projection not configured")
    path = Path(configured).resolve(strict=True)
    assert not path.is_relative_to(REPO), "keep real data outside the checkout"
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        handler.validate_projection(conn)
        yield conn
    finally:
        conn.close()


def test_tool_discovery_explains_the_link_boundary(
    handler: ModuleType,
) -> None:
    tools = {tool["name"]: tool for tool in handler.TOOLS}
    assert set(tools) == {"query_sql", "replica_status"}
    query = tools["query_sql"]["description"]
    assert "Empty results do not prove" in query
    assert "source email/view-receipt link" in query
    assert "ordering channel" in query
    assert "mailbox completeness" in tools["replica_status"]["description"]


def test_evidence_contract_does_not_promise_link_retrieval(
    handler: ModuleType,
) -> None:
    evidence = handler.LOOKUP_EVIDENCE
    assert evidence["mailbox_coverage"] == "unknown"
    assert evidence["raw_email_available"] is False
    assert evidence["receipt_links_available"] is False
    assert evidence["hosted_receipt_retrieval_available"] is False
    assert "Finding a link alone" in evidence["next_step"]
    assert "dine-in/pickup/delivery" in evidence["purchase_context"]


def test_real_projection_rows_are_unchanged(
    handler: ModuleType, real_projection: sqlite3.Connection
) -> None:
    sql = (
        "SELECT * FROM spend ORDER BY receipt_ref, item_description LIMIT 500"
    )
    expected = [list(row) for row in real_projection.execute(sql)]
    assert expected, "configured projection must contain real receipt items"
    result = handler.query_sql(real_projection, sql, 500)
    # Compare privately; pytest assertion rewriting must not dump receipt rows.
    same_rows = result["rows"] == expected
    assert same_rows, "receipt query changed rows (private values suppressed)"
    assert result["lookup_evidence"] == handler.LOOKUP_EVIDENCE


def test_real_projection_empty_result_retains_next_step(
    handler: ModuleType, real_projection: sqlite3.Connection
) -> None:
    result = handler.query_sql(
        real_projection, "SELECT receipt_ref FROM spend WHERE 0", 10
    )
    assert result["row_count"] == 0
    assert result["rows"] == []
    assert result["truncated"] is False
    assert (
        "does not establish"
        in result["lookup_evidence"]["empty_result_meaning"]
    )


def test_real_projection_ranges_keep_sources_separate(
    handler: ModuleType, real_projection: sqlite3.Connection
) -> None:
    status = handler._replica_status(real_projection)
    ranges = status["recorded_dates"]["ranges"]
    assert status["lookup_evidence"]["mailbox_coverage"] == "unknown"
    for name, table, column, where in (
        ("email_items", "spend", "date", "source = 'email'"),
        ("paper_items", "spend", "date", "source = 'paper'"),
        ("transactions", "txn", "txn_date", "1 = 1"),
    ):
        values = [
            row[0]
            for row in real_projection.execute(
                f"SELECT {column} FROM {table} WHERE {where}"
            )
        ]
        dated = sorted(value for value in values if value)
        expected = {
            "row_count": len(values),
            "oldest_recorded_date": dated[0] if dated else None,
            "newest_recorded_date": dated[-1] if dated else None,
            "undated_row_count": len(values) - len(dated),
        }
        matches = ranges[name] == expected
        assert matches, "date coverage mismatch (private values suppressed)"


def test_real_projection_boundary_still_denies_raw_mail(
    handler: ModuleType, real_projection: sqlite3.Connection
) -> None:
    result = handler.query_sql(
        real_projection, "SELECT name FROM sqlite_master", 10
    )
    assert "error" in result
    assert "nothing else is readable here" in result["error"]
