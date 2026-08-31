"""Repository-level gates for the Round A similarity harness."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO / "tests" / "fixtures" / "similarity"
PACKAGE_ROOT = REPO / "receipt_embeddings" / "receipt_embeddings"


def _load_golden() -> dict:
    path = FIXTURE_DIR / "golden.json"
    if not path.exists():
        pytest.skip("golden fixtures not generated yet")
    return json.loads(path.read_text(encoding="utf-8"))


def test_committed_fixtures_cover_three_families() -> None:
    golden = _load_golden()
    receipts = golden["receipts"]
    assert len(receipts) >= 40
    assert golden["meta"]["n_receipts"] == len(receipts)
    sources = {row["source_set"] for row in receipts}
    assert "line_items_golden" in sources
    assert "may26" in sources
    for receipt in receipts:
        merchant = receipt["merchant_resolution"]
        assert len(merchant["neighbors"]) <= merchant["top_k"] == 20
        assert "tier" in merchant and "decision" in merchant
        assert receipt["word_queries"]
        assert all(
            word["top_k"] == 30 and word["neighbors"]
            for word in receipt["word_queries"]
        )
        votes = receipt["section_verifier"]["votes"]
        assert set(votes) == {"AGREED", "DISAGREED", "ABSTAINED"}
        assert receipt["section_verifier"]["row_queries"]


def test_package_has_no_chromadb_import() -> None:
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "chromadb" or alias.name.startswith(
                        "chromadb."
                    ):
                        offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "chromadb" or module.startswith("chromadb."):
                    offenders.append(str(path))
    assert offenders == []


def test_evaluate_cli_fake_and_chroma_self_parity() -> None:
    evaluate = REPO / "scripts" / "similarity_harness" / "evaluate.py"
    if not (FIXTURE_DIR / "golden.json").exists():
        pytest.skip("golden fixtures not generated yet")
    for backend in ("fake", "chroma"):
        result = subprocess.run(
            [
                sys.executable,
                str(evaluate),
                "--backend",
                backend,
                "--fixtures",
                str(FIXTURE_DIR),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO),
        )
        assert result.returncode == 0, result.stderr
        scorecard = json.loads(result.stdout)
        metrics = scorecard["metrics"]
        assert metrics["neighbor_recall_at_k"]["macro"] == pytest.approx(1.0)
        assert metrics["merchant_agreement_pct"] == pytest.approx(100.0)
        assert scorecard["all_gates_pass"] is True
