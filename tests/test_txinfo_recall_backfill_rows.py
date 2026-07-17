from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from txinfo_recall_backfill_rows import derive_row_ids  # noqa: E402


def test_derive_row_ids_requires_exact_complete_rows() -> None:
    section = {"line_ids": [1, 2, 3]}
    rows = [
        {"SK": "RECEIPT#00001#ROW#00007", "line_ids": [1, 2]},
        {"SK": "RECEIPT#00001#ROW#00008", "line_ids": [3]},
    ]
    row_ids, anomalies = derive_row_ids(section, rows)
    assert row_ids == [7, 8]
    assert anomalies == []


def test_derive_row_ids_blocks_partial_row_coverage() -> None:
    section = {"line_ids": [1]}
    rows = [{"SK": "RECEIPT#00001#ROW#00007", "line_ids": [1, 2]}]
    _row_ids, anomalies = derive_row_ids(section, rows)
    assert {item["issue"] for item in anomalies} == {
        "section only partially covers visual row",
        "visual-row union does not equal section lines",
    }
