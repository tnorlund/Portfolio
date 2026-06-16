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
build_receipt_health_run_artifacts = build_viz_cache.build_receipt_health_run_artifacts
classify_receipt_health_issue_preflight = (
    build_viz_cache.classify_receipt_health_issue_preflight
)
eligible_receipt_health_issues = build_viz_cache.eligible_receipt_health_issues
reconcile_receipt_health_ledger = build_viz_cache.reconcile_receipt_health_ledger


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


def test_total_only_receipt_math_uses_has_total_equation():
    equations = build_viz_cache._build_equations(
        [
            {
                "issue": {
                    "line_id": 1,
                    "word_id": 1,
                    "word_text": "$12.34",
                    "current_label": "GRAND_TOTAL",
                },
                "llm_review": {},
            }
        ],
        {},
    )

    assert len(equations) == 1
    assert equations[0]["issue_type"] == "HAS_TOTAL"
    assert equations[0]["expected_value"] == 12.34
    assert equations[0]["actual_value"] == 12.34
    assert equations[0]["difference"] == 0


def test_build_receipt_health_issues_uses_stable_fingerprints():
    entry = build_receipt_health_entry(_base_within_entry())

    issues = build_receipt_health_issues(
        entry,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00.000+00:00",
    )
    later_issues = build_receipt_health_issues(
        entry,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00.000+00:00",
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
        observed_at="2026-01-01T00:00:00.000+00:00",
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
        observed_at="2026-01-01T00:00:00.000+00:00",
    )

    ledger = reconcile_receipt_health_ledger(
        None,
        run_issues,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00.000+00:00",
    )

    assert ledger["summary"]["by_state"] == {"open": 2}
    assert len(eligible_receipt_health_issues(ledger, limit=10)) == 0

    attempted = deepcopy(ledger)
    attempted["issues"][0]["state"] = "awaiting_validation"
    attempted["issues"][0]["attempt_count"] = 1
    attempted["issues"][0]["last_attempted_execution_id"] = "exec-1"
    attempted["issues"][0]["claimed_at"] = "2026-01-01T00:00:01.000+00:00"
    attempted["issues"][0]["claimed_by"] = "receipt-label-fixer"
    attempted["issues"][1]["claimed_at"] = "2026-01-01T00:00:02.000+00:00"
    attempted["issues"][1]["claimed_by"] = "stale-claim"

    still_failing = reconcile_receipt_health_ledger(
        attempted,
        run_issues,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00.000+00:00",
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
        observed_at="2026-01-03T00:00:00.000+00:00",
    )

    assert resolved["summary"]["by_state"] == {"resolved": 2}
    assert all(
        issue["resolved_execution_id"] == "exec-3" for issue in resolved["issues"]
    )


def test_reconcile_receipt_health_ledger_escalates_after_max_attempts():
    entry = build_receipt_health_entry(_base_within_entry())
    run_issues = build_receipt_health_issues(
        entry,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00.000+00:00",
    )
    ledger = reconcile_receipt_health_ledger(
        None,
        run_issues,
        execution_id="exec-1",
        observed_at="2026-01-01T00:00:00.000+00:00",
    )
    ledger["issues"][0]["state"] = "awaiting_validation"
    ledger["issues"][0]["attempt_count"] = 2

    reconciled = reconcile_receipt_health_ledger(
        ledger,
        run_issues,
        execution_id="exec-2",
        observed_at="2026-01-02T00:00:00.000+00:00",
        max_attempts=2,
    )

    escalated = next(
        issue
        for issue in reconciled["issues"]
        if issue["issue_id"] == ledger["issues"][0]["issue_id"]
    )
    assert escalated["state"] == "manual_review"
    assert "automated attempts" in escalated["blocked_reason"]


def _financial_issue(message="GRAND_TOTAL ($16.48) = sum(LINE_TOTAL) (0)"):
    return {
        "issue_id": "issue-1",
        "fingerprint": "fp-1",
        "image_id": "image-1",
        "receipt_id": 1,
        "check_id": "financial_math",
        "message": message,
    }


def _word(line_id, word_id, text, *, x=0.0, y=0.0, height=0.01):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "bounding_box": {
            "x": x,
            "y": y,
            "width": 0.05,
            "height": height,
        },
    }


def _label(line_id, word_id, label, status):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "label": label,
        "validation_status": status,
    }


def test_money_cents_preserves_negative_cents_sign():
    assert build_viz_cache._money_cents("-1.23") == -123
    assert build_viz_cache._money_cents("-$1.23") == -123
    assert build_viz_cache._money_cents("-3.00") == -300


def test_preflight_finds_safe_line_total_grand_total_plan():
    words = [
        _word(30, 1, "16.48"),
        _word(39, 1, "8.49"),
        _word(42, 1, "7.99"),
    ]
    labels = [
        _label(30, 1, "GRAND_TOTAL", "VALID"),
        _label(39, 1, "LINE_TOTAL", "INVALID"),
        _label(39, 1, "UNIT_PRICE", "VALID"),
        _label(42, 1, "LINE_TOTAL", "INVALID"),
        _label(42, 1, "UNIT_PRICE", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        _financial_issue(),
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "safe_exact_plan"
    assert preflight["lane"] == "safe_exact_plan"
    assert preflight["root_cause"] == "missing_line_totals"
    assert preflight["is_automation_ready"] is True
    assert preflight["evidence"]["line_total_sum"] == "16.48"
    assert [
        (action["line_id"], action["label"], action["new_status"])
        for action in preflight["proposed_actions"]
    ] == [
        (39, "LINE_TOTAL", "VALID"),
        (39, "UNIT_PRICE", "INVALID"),
        (42, "LINE_TOTAL", "VALID"),
        (42, "UNIT_PRICE", "INVALID"),
    ]


def test_preflight_includes_supporting_subtotal_and_tax_actions():
    words = [
        _word(21, 1, "$11.95"),
        _word(22, 1, "$3.15"),
        _word(23, 1, "$15.10"),
        _word(24, 1, "$1.09"),
        _word(25, 1, "$16.19"),
    ]
    labels = [
        _label(21, 1, "LINE_TOTAL", "INVALID"),
        _label(21, 1, "UNIT_PRICE", "VALID"),
        _label(22, 1, "LINE_TOTAL", "INVALID"),
        _label(22, 1, "UNIT_PRICE", "VALID"),
        _label(23, 1, "SUBTOTAL", "INVALID"),
        _label(24, 1, "TAX", "INVALID"),
        _label(25, 1, "GRAND_TOTAL", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        _financial_issue("GRAND_TOTAL ($16.19) = sum(LINE_TOTAL) (0)"),
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "safe_exact_plan"
    assert preflight["root_cause"] == "missing_line_totals_with_tax_discount"
    assert {
        (action["line_id"], action["label"], action["new_status"])
        for action in preflight["proposed_actions"]
    } == {
        (21, "LINE_TOTAL", "VALID"),
        (21, "UNIT_PRICE", "INVALID"),
        (22, "LINE_TOTAL", "VALID"),
        (22, "UNIT_PRICE", "INVALID"),
        (23, "SUBTOTAL", "VALID"),
        (24, "TAX", "VALID"),
    }


def test_preflight_math_mismatch_is_not_automation_ready():
    words = [
        _word(1, 1, "3.00"),
        _word(2, 1, "4.00"),
        _word(3, 1, "20.00"),
    ]
    labels = [
        _label(1, 1, "LINE_TOTAL", "INVALID"),
        _label(2, 1, "LINE_TOTAL", "INVALID"),
        _label(3, 1, "GRAND_TOTAL", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        _financial_issue("GRAND_TOTAL ($20.00) = sum(LINE_TOTAL) (0)"),
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "evaluator_rule_gap"
    assert preflight["root_cause"] == "line_total_candidates_do_not_reconcile"
    assert preflight["is_automation_ready"] is False

    ledger = {
        "max_attempts": 2,
        "issues": [
            {
                **_financial_issue(),
                "state": "open",
                "attempt_count": 0,
                "preflight": preflight,
            }
        ],
    }
    assert eligible_receipt_health_issues(ledger, limit=10) == []


def test_preflight_classifies_fragmented_item_prices_as_reocr_needed():
    issue = _financial_issue(
        "GRAND_TOTAL ($60.25) = sum(LINE_TOTAL) (0) + TAX ($5.05)"
    )
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 31,
                    "word_id": 1,
                    "word_text": "60.25",
                    "current_label": "GRAND_TOTAL",
                    "decision": "VALID",
                },
                {
                    "line_id": 28,
                    "word_id": 1,
                    "word_text": "5.05",
                    "current_label": "TAX",
                    "decision": "VALID",
                },
            ]
        }
    ]
    words = [
        _word(12, 1, "SPONGE", x=0.1, y=0.80),
        _word(13, 1, "6.", x=0.8, y=0.80),
        _word(14, 1, "ZIPLOC", x=0.1, y=0.78),
        _word(15, 1, "8.", x=0.8, y=0.78),
        _word(18, 1, "WIPES", x=0.1, y=0.76),
        _word(20, 1, "12.99", x=0.8, y=0.76),
        _word(25, 1, "TAX", x=0.1, y=0.72),
        _word(28, 1, "5.05", x=0.8, y=0.72),
        _word(30, 1, "BALANCE", x=0.1, y=0.70),
        _word(31, 1, "60.25", x=0.8, y=0.70),
    ]
    labels = [
        _label(20, 1, "LINE_TOTAL", "INVALID"),
        _label(28, 1, "TAX", "VALID"),
        _label(31, 1, "GRAND_TOTAL", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "reocr_needed"
    assert preflight["lane"] == "reocr_needed"
    assert preflight["root_cause"] == "line_item_price_tokenization_gap"
    evidence = preflight["evidence"]["line_item_amount_evidence"]
    assert evidence["item_amount_row_count"] == 3
    assert evidence["fragmented_amount_row_count"] == 2
    assert evidence["labeled_line_total_row_count"] == 1


def test_preflight_classifies_metadata_as_review_without_actions():
    issue = {
        "issue_id": "issue-merchant",
        "fingerprint": "fp-merchant",
        "image_id": "image-1",
        "receipt_id": 1,
        "check_id": "merchant_identity",
        "message": "STORE_HOURS on 'Kitchen' is INVALID",
        "evidence": [
            {
                "line_id": 4,
                "word_id": 2,
                "word_text": "Kitchen",
                "current_label": "STORE_HOURS",
                "decision": "INVALID",
            }
        ],
    }

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=[_word(4, 2, "Kitchen")],
        labels=[_label(4, 2, "STORE_HOURS", "INVALID")],
    )

    assert preflight["classification"] == "needs_ai_review"
    assert preflight["lane"] == "safe_label_edit_candidate"
    assert (
        preflight["root_cause"]
        == "business_name_token_mislabeled_store_hours"
    )
    assert preflight["is_automation_ready"] is False
    assert preflight["proposed_actions"] == []


def test_preflight_classifies_tip_gratuity_as_rule_gap():
    issue = _financial_issue(
        "GRAND_TOTAL ($16.19) = sum(LINE_TOTAL) (0) + TAX ($1.09) + TIP ($3.15)"
    )
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 10,
                    "word_id": 1,
                    "word_text": "$3.15",
                    "current_label": "TIP",
                    "decision": "INVALID",
                }
            ]
        }
    ]
    words = [
        _word(10, 1, "$3.15"),
        _word(11, 1, "$16.19"),
    ]
    labels = [
        _label(10, 1, "TIP", "INVALID"),
        _label(11, 1, "GRAND_TOTAL", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "receipt_structure_rule"
    assert preflight["root_cause"] == "missing_line_totals_with_tip_gratuity"
    assert preflight["is_automation_ready"] is False


def test_preflight_uses_decimal_percent_tip_section_for_tax_label():
    issue = _financial_issue(
        "GRAND_TOTAL ($17.5) = sum(LINE_TOTAL) (0) + TAX ($3.85)"
    )
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 24,
                    "word_id": 1,
                    "word_text": "$17.5",
                    "current_label": "GRAND_TOTAL",
                    "decision": "VALID",
                },
                {
                    "line_id": 45,
                    "word_id": 3,
                    "word_text": "$3.85",
                    "current_label": "TAX",
                    "decision": "VALID",
                },
            ]
        }
    ]
    words = [
        _word(24, 1, "Amount:", x=0.2, y=0.52),
        _word(24, 2, "$17.5", x=0.8, y=0.52),
        _word(36, 1, "Gratuity", x=0.1, y=0.20),
        _word(36, 2, "Suggestion", x=0.3, y=0.20),
        _word(45, 1, "22.00%", x=0.1, y=0.05),
        _word(45, 2, "-", x=0.35, y=0.05),
        _word(45, 3, "$3.85", x=0.5, y=0.05),
    ]
    labels = [
        _label(24, 2, "GRAND_TOTAL", "VALID"),
        _label(45, 3, "TAX", "VALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "receipt_structure_rule"
    assert preflight["root_cause"] == "tip_gratuity_ambiguity"
    assert preflight["is_automation_ready"] is False
    assert preflight["evidence"]["section_evidence"][
        "has_tip_suggestions"
    ] is True


def test_preflight_uses_tip_section_for_suggested_total_options():
    issue = _financial_issue("GRAND_TOTAL ($3.9,) = SUBTOTAL ($21.68)")
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 39,
                    "word_id": 3,
                    "word_text": "$3.9,",
                    "current_label": "GRAND_TOTAL",
                    "decision": "INVALID",
                },
                {
                    "line_id": 37,
                    "word_id": 1,
                    "word_text": "$21.68",
                    "current_label": "SUBTOTAL",
                    "decision": "INVALID",
                },
            ]
        }
    ]
    words = [
        _word(36, 1, "Subtotal", x=0.1, y=0.30),
        _word(37, 1, "$21.68", x=0.8, y=0.30),
        _word(38, 1, "ADD", x=0.1, y=0.25),
        _word(38, 2, "TIPS", x=0.2, y=0.25),
        _word(39, 1, "18%", x=0.1, y=0.20),
        _word(39, 2, "(Tips", x=0.25, y=0.20),
        _word(39, 3, "$3.9,", x=0.45, y=0.20),
        _word(39, 4, "Total", x=0.6, y=0.20),
        _word(39, 5, "$25.58)", x=0.75, y=0.20),
    ]
    labels = [
        _label(37, 1, "SUBTOTAL", "INVALID"),
        _label(39, 3, "GRAND_TOTAL", "INVALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "receipt_structure_rule"
    assert preflight["root_cause"] == "tip_gratuity_ambiguity"


def test_preflight_uses_visual_rows_for_voided_item_formula_gap():
    issue = _financial_issue(
        "GRAND_TOTAL ($7.48) = sum(LINE_TOTAL) (3.00 + 3.00 + 1.49 + 2.99) - DISCOUNT (-3.00)"
    )
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 26,
                    "word_id": 1,
                    "word_text": "-3.00",
                    "current_label": "DISCOUNT",
                    "decision": "VALID",
                }
            ]
        }
    ]
    words = [
        _word(16, 1, "Voided", x=0.05, y=0.725),
        _word(16, 2, "Item", x=0.20, y=0.725),
        _word(17, 1, "BOILER", x=0.05, y=0.713),
        _word(17, 2, "ONIONS", x=0.20, y=0.713),
        _word(26, 1, "-3.00", x=0.80, y=0.709),
    ]
    labels = [_label(26, 1, "DISCOUNT", "VALID")]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "receipt_structure_rule"
    assert preflight["root_cause"] == "void_discount_formula_rule"
    assert preflight["evidence"]["section_evidence"][
        "has_void_discount"
    ] is True


def test_preflight_uses_tip_entry_area_for_payment_tip_ambiguity():
    issue = _financial_issue("GRAND_TOTAL ($74.44) = SUBTOTAL (80.49)")
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 43,
                    "word_id": 1,
                    "word_text": "$74.44",
                    "current_label": "GRAND_TOTAL",
                    "decision": "INVALID",
                },
                {
                    "line_id": 54,
                    "word_id": 2,
                    "word_text": "80.49",
                    "current_label": "SUBTOTAL",
                    "decision": "INVALID",
                },
            ]
        }
    ]
    words = [
        _word(43, 1, "$74.44", x=0.65, y=0.40),
        _word(50, 1, "Total:", x=0.15, y=0.40),
        _word(51, 1, "Tip:", x=0.15, y=0.39),
        _word(54, 1, "Subtotal:", x=0.15, y=0.38),
        _word(54, 2, "80.49", x=0.65, y=0.38),
    ]
    labels = [
        _label(43, 1, "GRAND_TOTAL", "INVALID"),
        _label(54, 2, "SUBTOTAL", "INVALID"),
    ]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "evaluator_rule_gap"
    assert preflight["lane"] == "receipt_structure_rule"
    assert preflight["root_cause"] == "tip_gratuity_ambiguity"
    assert preflight["evidence"]["section_evidence"][
        "has_tip_entry_area"
    ] is True


def test_preflight_classifies_malformed_amount_as_reocr_needed():
    issue = _financial_issue("GRAND_TOTAL ($0,72) = SUBTOTAL ($0.72)")
    issue["evidence"] = [
        {
            "involved_words": [
                {
                    "line_id": 1,
                    "word_id": 1,
                    "word_text": "$0,72",
                    "current_label": "GRAND_TOTAL",
                    "decision": "INVALID",
                }
            ]
        }
    ]
    words = [_word(1, 1, "$0,72")]
    labels = [_label(1, 1, "GRAND_TOTAL", "INVALID")]

    preflight = classify_receipt_health_issue_preflight(
        issue,
        words=words,
        labels=labels,
    )

    assert preflight["classification"] == "reocr_needed"
    assert preflight["lane"] == "reocr_needed"
    assert preflight["root_cause"] == "malformed_amount_text"
    assert preflight["is_automation_ready"] is False
