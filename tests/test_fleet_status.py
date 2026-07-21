"""Fleet-status v1 contract: truthful table, cross-check, exit codes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from receipt_dynamo.entities.merchant_truth import MerchantTruthActive
from synthesis_loop import fleet_status

NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


class StubReader:
    """MerchantTruthReader protocol stub (same shape as loader FakeReader)."""

    def __init__(
        self, active_records: list[MerchantTruthActive] | None = None
    ) -> None:
        self.active_records = active_records or []
        self.fleet_calls = 0

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]:
        self.fleet_calls += 1
        return self.active_records

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        raise AssertionError("fleet_status must not read per-slug pointers")

    def read_merchant_truth_bundle_items(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[dict[str, Any]]:
        raise AssertionError("fleet_status must not read bundle items")


def make_active(
    slug: str = "vons",
    *,
    version: int = 1,
    bundle_hash: str = HASH_A,
    gate_status: str = "PASS",
    activated_at: str = "2026-07-18T12:00:00+00:00",
) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=slug,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=[slug],
        activated_at=activated_at,
        activated_by="owner",
        gate_status=gate_status,
    )


def test_legacy_enumerations_union_to_sixteen_merchants() -> None:
    legacy = fleet_status.load_legacy_merchants()
    assert len(legacy) == 16
    assert "vons" in legacy
    assert "sprouts_farmers_market" in legacy
    # env.mjs-only presence never adds merchants beyond the profile keys,
    # but every FONT_MERCHANTS value must resolve into the union.
    dual = [
        slug
        for slug, entry in legacy.items()
        if fleet_status.FONT_MERCHANTS_SOURCE in entry["sources"]
    ]
    assert len(dual) == 11


def test_empty_fleet_renders_zero_active_sixteen_missing_exit_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([])

    exit_code = fleet_status.main([], reader=reader, now=NOW)

    assert exit_code == 0
    assert reader.fleet_calls == 1
    output = capsys.readouterr().out
    assert "**0 ACTIVE / 16 missing**" in output
    assert "(no ACTIVE merchant-truth rows)" in output
    assert "| vons | Vons |" in output
    assert "| sprouts_farmers_market | Sprouts Farmers Market |" in output


def test_populated_fleet_renders_row_with_short_hash_and_staleness(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader(
        [make_active("vons", activated_at="2026-07-18T12:00:00+00:00")]
    )

    exit_code = fleet_status.main([], reader=reader, now=NOW)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "**1 ACTIVE / 15 missing**" in output
    assert f"| vons | 1 | `{'a' * 12}` | PASS " in output
    assert "| 2026-07-18T12:00:00+00:00 | 3 |" in output
    # vons moved out of the missing list
    assert "| vons | Vons |" not in output


def test_json_output_is_machine_readable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([make_active("vons"), make_active("cvs")])

    exit_code = fleet_status.main(["--json"], reader=reader, now=NOW)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["active_count"] == 2
    assert report["missing_count"] == 14
    assert report["legacy_count"] == 16
    assert [row["slug"] for row in report["active"]] == ["cvs", "vons"]
    row = report["active"][1]
    assert row["bundle_hash"] == HASH_A
    assert row["bundle_hash_short"] == "a" * 12
    assert row["gate_status"] == "PASS"
    assert row["staleness_days"] == 3
    assert report["check_failures"] == []
    assert report["unlisted_in_legacy"] == []


def test_active_slug_outside_legacy_is_surfaced_not_fatal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([make_active("mystery_mart")])

    exit_code = fleet_status.main(["--json"], reader=reader, now=NOW)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["missing_count"] == 16
    assert report["unlisted_in_legacy"] == ["mystery_mart"]


def test_mixed_gate_statuses_display_without_check_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader(
        [
            make_active("vons"),
            make_active("cvs", bundle_hash=HASH_B, gate_status="FAIL"),
        ]
    )

    exit_code = fleet_status.main(["--json"], reader=reader, now=NOW)

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["check_failures"] == ["cvs"]


def test_check_flag_exits_nonzero_on_non_pass_gate() -> None:
    reader = StubReader(
        [
            make_active("vons"),
            make_active("cvs", bundle_hash=HASH_B, gate_status="FAIL"),
        ]
    )

    assert fleet_status.main(["--check"], reader=reader, now=NOW) == 1


def test_check_flag_exits_zero_when_all_active_pass() -> None:
    reader = StubReader([make_active("vons")])

    assert fleet_status.main(["--check"], reader=reader, now=NOW) == 0


def test_check_flag_exits_zero_on_empty_fleet() -> None:
    assert fleet_status.main(["--check"], reader=StubReader()) == 0


def test_prod_table_flag_is_refused_before_any_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([make_active("vons")])

    exit_code = fleet_status.main(
        ["--table", "ReceiptsTable-d7ff76a"], reader=reader, now=NOW
    )

    assert exit_code == 2
    assert reader.fleet_calls == 0
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert captured.out == ""


def test_prod_table_env_var_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    reader = StubReader()

    assert fleet_status.main([], reader=reader) == 2
    assert reader.fleet_calls == 0


def test_default_table_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    assert fleet_status.resolve_table(None) == "ReceiptsTable-dc5be22"


def test_prod_marker_variants_are_refused() -> None:
    with pytest.raises(ValueError, match="refusing"):
        fleet_status.resolve_table("ReceiptsTable-d7ff76a-copy")
