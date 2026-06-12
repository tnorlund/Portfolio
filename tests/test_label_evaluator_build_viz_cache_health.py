import importlib.util
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
