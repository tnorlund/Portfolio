import importlib.util
from copy import deepcopy
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "label_evaluator_step_functions"
    / "lambdas"
    / "build_viz_cache.py"
)
SPEC = importlib.util.spec_from_file_location("build_viz_cache", MODULE_PATH)
assert SPEC and SPEC.loader
build_viz_cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_viz_cache)

build_receipt_health_entry = build_viz_cache.build_receipt_health_entry
build_receipt_health_issues = build_viz_cache.build_receipt_health_issues
build_receipt_health_run_artifacts = (
    build_viz_cache.build_receipt_health_run_artifacts
)
eligible_receipt_health_issues = build_viz_cache.eligible_receipt_health_issues
reconcile_receipt_health_ledger = (
    build_viz_cache.reconcile_receipt_health_ledger
)


def _base_within_entry():
    return {
        "image_id": "img-1",
        "receipt_id": 1,
        "merchant_name": "Example Store",
        "trace_id": "trace-1",
        "receipt_type": "itemized",
        "place_validation": {
            "place": {"merchant_name": "Example Store"},
            "decisions": [{"decision": "VALID"}],
            "summary": {
                "total": 1,
                "valid": 1,
                "invalid": 0,
                "needs_review": 0,
            },
            "duration_seconds": 1.2,
            "is_llm": True,
        },
        "format_validation": {
            "decisions": [{"decision": "NEEDS_REVIEW"}],
            "summary": {
                "total": 1,
                "valid": 0,
                "invalid": 0,
                "needs_review": 1,
            },
            "duration_seconds": 0.8,
            "is_llm": True,
        },
        "financial_math": {
            "equations": [
                {
                    "issue_type": "GRAND_TOTAL",
                    "difference": 2.34,
                    "involved_words": [
                        {"line_id": 1, "word_id": 1},
                        {"line_id": 2, "word_id": 1},
                    ],
                }
            ],
            "summary": {
                "total_equations": 1,
                "has_invalid": False,
                "has_needs_review": False,
            },
            "duration_seconds": 1.5,
            "is_llm": True,
        },
        "words": [],
        "width": 600,
        "height": 1200,
        "cdn_s3_key": "receipts/img-1.jpg",
    }


def test_build_receipt_health_entry_summarizes_checks():
    entry = build_receipt_health_entry(_base_within_entry())

    assert entry["overall_status"] == "fail"
    assert entry["summary"] == {
        "total_checks": 3,
        "passed": 1,
        "needs_review": 1,
        "failed": 1,
        "not_applicable": 0,
        "issue_count": 2,
    }
    assert [check["id"] for check in entry["checks"]] == [
        "merchant_identity",
        "receipt_format",
        "financial_math",
    ]
    assert entry["checks"][2]["summary"]["mismatched_equations"] == 1
    assert entry["primary_issues"][0]["check_id"] == "financial_math"
    assert entry["primary_issues"][0]["message"] == "1 equations, 1 mismatches"
    assert entry["cdn_s3_key"] == "receipts/img-1.jpg"


def test_build_receipt_health_entry_handles_missing_evidence():
    source = _base_within_entry()
    source["place_validation"]["summary"] = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "needs_review": 0,
    }
    source["place_validation"]["decisions"] = []
    source["format_validation"]["summary"] = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "needs_review": 0,
    }
    source["format_validation"]["decisions"] = []
    source["financial_math"]["equations"] = []

    entry = build_receipt_health_entry(source)

    assert entry["overall_status"] == "not_applicable"
    assert entry["summary"]["not_applicable"] == 3
    assert entry["summary"]["issue_count"] == 0
    assert entry["primary_issues"] == []


def test_build_receipt_health_entry_passes_when_some_checks_not_applicable():
    source = _base_within_entry()
    source["format_validation"]["summary"] = {
        "total": 1,
        "valid": 1,
        "invalid": 0,
        "needs_review": 0,
    }
    source["financial_math"]["equations"] = []

    entry = build_receipt_health_entry(source)

    assert entry["overall_status"] == "pass"
    assert entry["summary"]["passed"] == 2
    assert entry["summary"]["not_applicable"] == 1


def test_build_receipt_health_issues_uses_stable_fingerprints():
    entry = build_receipt_health_entry(_base_within_entry())

    issues = build_receipt_health_issues(
        entry,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    later_issues = build_receipt_health_issues(
        entry,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00+00:00",
    )

    assert [issue["check_id"] for issue in issues] == [
        "financial_math",
        "receipt_format",
    ]
    assert issues[0]["status"] == "fail"
    assert issues[0]["issue_type"] == "GRAND_TOTAL"
    assert issues[0]["issue_id"] == later_issues[0]["issue_id"]
    assert issues[1]["status"] == "review"
    assert issues[1]["issue_id"] == later_issues[1]["issue_id"]


def test_build_receipt_health_run_artifacts_summarizes_execution():
    entry = build_receipt_health_entry(_base_within_entry())

    issues, summary = build_receipt_health_run_artifacts(
        [entry],
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )

    assert len(issues) == 2
    assert summary["execution_id"] == "exec-1"
    assert summary["total_receipts"] == 1
    assert summary["total_issues"] == 2
    assert summary["receipts_with_issues"] == 1
    assert summary["by_check"]["financial_math"]["fail"] == 1
    assert summary["by_issue_type"]["GRAND_TOTAL"] == 1


def test_reconcile_receipt_health_ledger_tracks_attempts_and_resolution():
    entry = build_receipt_health_entry(_base_within_entry())
    run_issues = build_receipt_health_issues(
        entry,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )

    ledger = reconcile_receipt_health_ledger(
        None,
        run_issues,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )

    assert ledger["summary"]["by_state"] == {"open": 2}
    assert len(eligible_receipt_health_issues(ledger, limit=10)) == 2

    attempted = deepcopy(ledger)
    attempted["issues"][0]["state"] = "awaiting_validation"
    attempted["issues"][0]["attempt_count"] = 1
    attempted["issues"][0]["last_attempted_execution_id"] = "exec-1"
    attempted["issues"][0]["claimed_at"] = "2026-01-01T00:00:01+00:00"
    attempted["issues"][0]["claimed_by"] = "receipt-label-fixer"
    attempted["issues"][1]["claimed_at"] = "2026-01-01T00:00:02+00:00"
    attempted["issues"][1]["claimed_by"] = "stale-claim"

    still_failing = reconcile_receipt_health_ledger(
        attempted,
        run_issues,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00+00:00",
    )

    retried_issue = next(
        issue
        for issue in still_failing["issues"]
        if issue["issue_id"] == attempted["issues"][0]["issue_id"]
    )
    assert retried_issue["state"] == "open"
    assert retried_issue["attempt_count"] == 1
    assert retried_issue["last_validation_execution_id"] == "exec-2"
    assert "claimed_at" not in retried_issue
    assert "claimed_by" not in retried_issue
    open_issue = next(
        issue
        for issue in still_failing["issues"]
        if issue["issue_id"] == attempted["issues"][1]["issue_id"]
    )
    assert open_issue["state"] == "open"
    assert "claimed_at" not in open_issue
    assert "claimed_by" not in open_issue

    resolved = reconcile_receipt_health_ledger(
        still_failing,
        [],
        execution_id="exec-3",
        observed_at="2026-01-03T00:00:00+00:00",
    )

    assert resolved["summary"]["by_state"] == {"resolved": 2}
    assert all(
        issue["resolved_execution_id"] == "exec-3"
        for issue in resolved["issues"]
    )


def test_reconcile_receipt_health_ledger_escalates_after_max_attempts():
    entry = build_receipt_health_entry(_base_within_entry())
    run_issues = build_receipt_health_issues(
        entry,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    ledger = reconcile_receipt_health_ledger(
        None,
        run_issues,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00+00:00",
    )
    ledger["issues"][0]["state"] = "awaiting_validation"
    ledger["issues"][0]["attempt_count"] = 2

    reconciled = reconcile_receipt_health_ledger(
        ledger,
        run_issues,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00+00:00",
        max_attempts=2,
    )

    escalated = next(
        issue
        for issue in reconciled["issues"]
        if issue["issue_id"] == ledger["issues"][0]["issue_id"]
    )
    assert escalated["state"] == "manual_review"
    assert "automated attempts" in escalated["blocked_reason"]
