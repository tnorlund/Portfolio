"""capture_golden.py: synthetic determinism, no live run without creds."""

from __future__ import annotations

import filecmp
import os
import subprocess
import sys
from pathlib import Path

import pytest

from receipt_embeddings.fixtures import load_fixture_bundle
from receipt_embeddings.harness import chroma_cloud_credentials
from receipt_embeddings.synthetic import (
    golden_receipts,
    write_synthetic_fixtures,
)

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_CAPTURE = _REPO / "scripts" / "similarity_harness" / "capture_golden.py"


def test_golden_set_has_line_items_and_may26() -> None:
    receipts = golden_receipts()
    assert len(receipts) >= 40
    sources = {row["source"] for row in receipts}
    assert "line_items_golden" in sources
    assert "may26_batch" in sources
    assert sum(1 for row in receipts if row["source"] == "may26_batch") == 43


def test_two_synthetic_captures_are_identical(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    write_synthetic_fixtures(first)
    write_synthetic_fixtures(second)
    names = [
        "golden_set.json",
        "merchant_resolution.json",
        "word_neighbors.json",
        "section_verifier.json",
        "vectors.json",
    ]
    for name in names:
        assert filecmp.cmp(first / name, second / name, shallow=False)


def test_synthetic_bundle_has_three_families(tmp_path: Path) -> None:
    write_synthetic_fixtures(tmp_path)
    bundle = load_fixture_bundle(tmp_path)
    assert len(bundle["merchant_resolution"]["queries"]) >= 40
    assert bundle["word_neighbors"]["queries"]
    assert bundle["section_verifier"]["queries"]
    votes = {
        vote["vote"]
        for receipt in bundle["section_verifier"]["queries"]
        for vote in receipt["votes"]
    }
    assert votes <= {"AGREED", "DISAGREED", "ABSTAINED"}
    assert votes
    tiers = {q["tier"] for q in bundle["merchant_resolution"]["queries"]}
    assert tiers <= {
        "chroma_phone",
        "chroma_address",
        "chroma_text",
        "unresolved",
    }


def test_capture_cli_synthetic(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_CAPTURE),
            "--synthetic",
            "--out",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "golden_set.json").is_file()


def test_capture_cli_refuses_live_without_creds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHROMA_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("CHROMA_CLOUD_TENANT", raising=False)
    monkeypatch.delenv("CHROMA_CLOUD_DATABASE", raising=False)
    assert chroma_cloud_credentials() is None
    env = os.environ.copy()
    env["CHROMA_CLOUD_API_KEY"] = ""
    env["CHROMA_CLOUD_TENANT"] = ""
    env["CHROMA_CLOUD_DATABASE"] = ""
    result = subprocess.run(
        [sys.executable, str(_CAPTURE), "--out", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=_REPO,
        env=env,
    )
    assert result.returncode != 0
    assert "Refusing live capture" in (result.stderr + result.stdout)
