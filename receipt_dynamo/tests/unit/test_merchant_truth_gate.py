"""Unit coverage for the MERCHANT_TRUTH_GATE entity (contract section 7.6)."""

import pytest

from receipt_dynamo.entities.merchant_truth_gate import (
    MerchantTruthGateRecord,
    gate_version_segment,
)

pytestmark = pytest.mark.unit

SLUG = "costco-wholesale"
RUN_AT = "2026-07-22T10:00:00+00:00"
HASH = "a" * 64


def make(**overrides):
    kwargs = dict(
        slug=SLUG,
        run_at=RUN_AT,
        version=1,
        bundle_hash=HASH,
        eval_git_sha="deadbeefcafe",
        overall="PASS",
        per_metric=[
            {"metric": "columns", "verdict": "PASS"},
            {"metric": "logo", "verdict": "PASS"},
        ],
        gaps=[],
        coverage=[],
        evidence_refs=["/out/costco.checks.json"],
        receipt_tested={"image_id": "img-1", "receipt_id": 1},
    )
    kwargs.update(overrides)
    return MerchantTruthGateRecord(**kwargs)


def test_key_grammar_and_type():
    record = make()
    assert record.key["PK"]["S"] == f"MERCHANT_TRUTH#{SLUG}"
    # GATE#{run_at}#v{n:010d}; the version segment reuses the key grammar.
    assert record.key["SK"]["S"] == f"GATE#{RUN_AT}#v0000000001"
    assert record.to_item()["TYPE"]["S"] == "MERCHANT_TRUTH_GATE"


def test_gate_version_segment_zero_pads():
    assert gate_version_segment(7) == "v0000000007"
    with pytest.raises(ValueError):
        gate_version_segment(0)


def test_bundle_map_carries_version_and_hash():
    item = make(version=3).to_item()
    assert item["bundle"]["M"]["version"]["N"] == "3"
    assert item["bundle"]["M"]["hash"]["S"] == HASH


def test_roundtrip_equal():
    record = make(
        version=2,
        overall="PASS_WITH_GAPS",
        per_metric=[
            {"metric": "columns", "verdict": "PASS"},
            {"metric": "logo", "verdict": "PASS_WITH_GAPS"},
        ],
        gaps=[
            {
                "metric": "logo",
                "verdict": "PASS_WITH_GAPS",
                "detail": {"score": 0.4, "note": "faint"},
            }
        ],
        coverage=["columns:top:price", "style:HEADER"],
    )
    restored = MerchantTruthGateRecord.from_item(record.to_item())
    assert restored == record
    # detail is preserved verbatim as a map (the bridge shape), not a string.
    assert restored.gaps[0]["detail"] == {"score": 0.4, "note": "faint"}
    assert restored.coverage == ["columns:top:price", "style:HEADER"]


def test_fail_run_may_carry_gaps_as_work_list():
    # FAIL is unconstrained by the PASS_WITH_GAPS iff; its gaps are the work
    # list for closing the version.
    record = make(
        overall="FAIL",
        per_metric=[
            {"metric": "columns", "verdict": "PASS"},
            {"metric": "logo", "verdict": "FAIL"},
        ],
        gaps=[
            {
                "metric": "logo",
                "verdict": "FAIL",
                "detail": {"reason": "no logo drawn"},
            }
        ],
    )
    assert record.overall == "FAIL"
    assert len(record.gaps) == 1


def test_from_item_normalizes_legacy_per_metric_map():
    # A pre-bridge record stored per_metric as a {metric: verdict} MAP. The
    # reader normalizes it to the canonical sorted list so one legacy record
    # can never break list_gate_records; the write path never emits a map.
    item = make(
        overall="FAIL",
        per_metric=[{"metric": "columns", "verdict": "FAIL"}],
        gaps=[{"metric": "columns", "verdict": "FAIL", "detail": {}}],
    ).to_item()
    item["per_metric"] = {
        "M": {
            "logo": {"S": "PASS"},
            "columns": {"S": "FAIL"},
        }
    }
    restored = MerchantTruthGateRecord.from_item(item)
    assert restored.per_metric == [
        {"metric": "columns", "verdict": "FAIL"},
        {"metric": "logo", "verdict": "PASS"},
    ]


def test_coverage_paths_live_outside_gaps():
    # A PASS run may still carry coverage paths (sub-metric untested lanes);
    # they are not gaps, so the PASS-carries-no-gaps rule is not tripped.
    record = make(overall="PASS", gaps=[], coverage=["columns:top:price"])
    assert record.gaps == []
    assert record.coverage == ["columns:top:price"]


# --- the section 7.5/7.6 PASS_WITH_GAPS-iff-gaps invariant, at construction ---


def test_pass_with_gaps_requires_non_empty_gaps():
    with pytest.raises(ValueError, match="PASS_WITH_GAPS requires"):
        make(overall="PASS_WITH_GAPS", gaps=[])


def test_pass_may_not_carry_gaps():
    with pytest.raises(ValueError, match="PASS may not carry gaps"):
        make(
            overall="PASS",
            gaps=[{"metric": "logo", "verdict": "FAIL", "detail": "x"}],
        )


def test_pass_verdict_may_not_appear_in_gaps():
    with pytest.raises(ValueError, match="PASS verdict may not appear"):
        make(
            overall="PASS_WITH_GAPS",
            gaps=[{"metric": "logo", "verdict": "PASS", "detail": "x"}],
        )


def test_rejects_unknown_overall():
    with pytest.raises(ValueError, match="invalid gate overall"):
        make(overall="MAYBE")


def test_rejects_bad_hash():
    with pytest.raises(ValueError):
        make(bundle_hash="nothex")


def test_rejects_empty_per_metric():
    with pytest.raises(ValueError, match="per_metric must be a non-empty"):
        make(per_metric=[])


def test_rejects_per_metric_wrong_shape():
    # The old dict[str, str] shape is rejected -- per_metric is the bridge's
    # list[{metric, verdict}].
    with pytest.raises(ValueError, match="per_metric must be a non-empty"):
        make(per_metric={"columns": "PASS"})
    with pytest.raises(ValueError, match="per_metric.metric"):
        make(per_metric=[{"verdict": "PASS"}])


def test_rejects_bad_coverage():
    with pytest.raises(ValueError, match="coverage entries"):
        make(coverage=["ok", ""])


def test_rejects_gap_missing_detail():
    with pytest.raises(ValueError, match="detail"):
        make(
            overall="PASS_WITH_GAPS",
            gaps=[{"metric": "logo", "verdict": "FAIL"}],
        )


def test_rejects_empty_receipt_tested():
    with pytest.raises(ValueError, match="receipt_tested"):
        make(receipt_tested=None)


def test_rejects_bad_slug_and_version():
    with pytest.raises(ValueError):
        make(slug="Bad Slug")
    with pytest.raises(ValueError):
        make(version=0)
