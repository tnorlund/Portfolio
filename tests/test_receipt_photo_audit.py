"""Coverage claims must survive partial scans, stale sources and retries."""

# Test names describe the observable invariant.
# pylint: disable=missing-function-docstring

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import receipt_photo_audit as audit


def inputs():
    """One reviewed source with a corresponding dev receipt and summary."""
    library = {
        "schema_version": 1,
        "scope": "full",
        "enumeration_complete": True,
        "expected_assets": 1,
        "assets": [
            {
                "asset_key": "library:one",
                "revision": "sha256:abc",
                "classification": "receipt",
                "evidence": ["source.json"],
            }
        ],
    }
    ledger = {
        "schema_version": 1,
        "photos": [
            {
                "asset_key": "library:one",
                "revision": "sha256:abc",
                "image_id": "image-one",
                "receipt_ids": [1],
                "verification": {
                    "revision": "sha256:abc",
                    "receipt_ids": [1],
                    "result": "passed",
                    "checked_snapshot_at": "2026-09-07T12:00:00+00:00",
                    "evidence": ["mcp-final.json"],
                },
            }
        ],
    }
    project = {
        "schema_version": 1,
        "environment": "dev",
        "complete": True,
        "snapshot_at": "2026-09-07T12:00:00+00:00",
        "images": [{"image_id": "image-one"}],
        "receipts": [{"image_id": "image-one", "receipt_id": 1}],
        "summaries": [{"image_id": "image-one", "receipt_id": 1}],
    }
    ledger["photos"][0]["verification"]["project_fingerprint"] = (
        audit.receipt_fingerprint(project, "image-one", [1])
    )
    return library, ledger, project


def test_complete_verified_inventory():
    result = audit.coverage_report(*inputs())
    assert result["all_receipts_processed"] is True
    assert result["counts"] == {"verified": 1}
    assert result["cleanup_authorized"] is False


@pytest.mark.parametrize("change", ["partial", "unfinished", "missing"])
def test_partial_enumeration_never_claims_complete(change):
    library, ledger, project = inputs()
    if change == "partial":
        library["scope"] = "partial"
    elif change == "unfinished":
        library["enumeration_complete"] = False
    else:
        library["expected_assets"] = 2
    assert not audit.coverage_report(library, ledger, project)[
        "all_receipts_processed"
    ]


def test_unresolved_library_counts_block_even_matching_manifest_count():
    library, ledger, project = inputs()
    library["enumeration_issues"] = [
        "Footer and Select All disagree on the number of photo assets"
    ]
    result = audit.coverage_report(library, ledger, project)
    assert not result["library_enumeration_complete"]
    assert not result["all_receipts_processed"]
    assert result["enumeration_issues"] == library["enumeration_issues"]
    assert result["counts"] == {"verified": 1}
    library["enumeration_issues"] = []
    assert audit.coverage_report(library, ledger, project)[
        "all_receipts_processed"
    ]


@pytest.mark.parametrize("issues", [None, "blocked", False, [""], [" "], [1]])
def test_malformed_enumeration_issues_are_rejected(issues):
    library, ledger, project = inputs()
    library["enumeration_issues"] = issues
    with pytest.raises(audit.AuditError, match="enumeration_issues"):
        audit.coverage_report(library, ledger, project)


@pytest.mark.parametrize("classification", ["uncertain", "unavailable"])
def test_unreadable_or_uncertain_photo_blocks_coverage(classification):
    library, ledger, project = inputs()
    library["assets"][0]["classification"] = classification
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"unresolved": 1}
    assert not result["all_receipts_processed"]


def test_classification_without_evidence_is_unresolved():
    library, ledger, project = inputs()
    library["assets"][0].update(classification="not_receipt", evidence=[])
    assert audit.coverage_report(library, ledger, project)["counts"] == {
        "unresolved": 1
    }


def test_changed_source_needs_review_without_reupload():
    library, ledger, project = inputs()
    library["assets"][0]["revision"] = "new-version"
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"source_changed": 1}
    assert not result["all_receipts_processed"]


@pytest.mark.parametrize(
    "damage",
    [
        "image",
        "receipt",
        "summary",
        "extra",
        "evidence",
        "revision",
        "receipt_ids",
    ],
)
def test_import_existence_is_not_verification(damage):
    library, ledger, project = inputs()
    if damage in {"image", "receipt", "summary"}:
        project[
            {"image": "images", "receipt": "receipts", "summary": "summaries"}[
                damage
            ]
        ] = []
    elif damage == "extra":
        project["receipts"].append({"image_id": "image-one", "receipt_id": 2})
    else:
        ledger["photos"][0]["verification"][damage] = []
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"imported_needs_review": 1}
    assert not result["all_receipts_processed"]


@pytest.mark.parametrize(
    "state", ["in_flight", "uncertain", "unrecognized-state"]
)
def test_interrupted_upload_cannot_be_offered_for_retry(state):
    library, ledger, project = inputs()
    ledger["photos"][0] = {
        "asset_key": "library:one",
        "revision": "sha256:abc",
        "upload_state": state,
    }
    assert audit.coverage_report(library, ledger, project)["counts"] == {
        "upload_unresolved": 1
    }


def test_unimported_and_non_receipt_are_distinct():
    library, ledger, project = inputs()
    ledger["photos"] = []
    assert audit.coverage_report(library, ledger, project)["counts"] == {
        "not_imported": 1
    }
    library["assets"][0]["classification"] = "not_receipt"
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"not_receipt": 1}
    assert result["all_receipts_processed"]


def test_duplicate_requires_review_and_verified_target():
    library, ledger, project = inputs()
    asset = deepcopy(library["assets"][0])
    asset["asset_key"] = "library:two"
    library["assets"].append(asset)
    library["expected_assets"] = 2
    duplicate = {
        "asset_key": "library:two",
        "revision": "sha256:abc",
        "duplicate_of": "library:one",
        "duplicate_evidence": ["side-by-side-review.json"],
    }
    ledger["photos"].append(duplicate)
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"verified": 1, "duplicate_verified": 1}
    assert result["distinct_verified_images"] == 1
    assert result["all_receipts_processed"]
    duplicate["duplicate_evidence"] = []
    assert not audit.coverage_report(library, ledger, project)[
        "all_receipts_processed"
    ]


def test_duplicate_cycle_cannot_be_certified():
    library, ledger, project = inputs()
    ledger["photos"][0].update(
        duplicate_of="library:one", duplicate_evidence=["review.json"]
    )
    assert audit.coverage_report(library, ledger, project)["counts"] == {
        "duplicate_unresolved": 1
    }


def test_duplicate_source_identity_rejected():
    library, ledger, project = inputs()
    library["assets"].append(deepcopy(library["assets"][0]))
    with pytest.raises(audit.AuditError, match="Duplicate"):
        audit.coverage_report(library, ledger, project)


def test_incomplete_or_wrong_environment_snapshot_rejected():
    library, ledger, project = inputs()
    project["complete"] = False
    with pytest.raises(audit.AuditError):
        audit.coverage_report(library, ledger, project)
    project.update(complete=True, environment="prod")
    with pytest.raises(audit.AuditError):
        audit.coverage_report(library, ledger, project)


def test_pagination_includes_last_page_and_rejects_cursor_loop():
    calls = []

    def pages(**kwargs):
        calls.append(kwargs)
        if kwargs["last_evaluated_key"] is None:
            return [SimpleNamespace(value=1)], {"PK": "next"}
        return [SimpleNamespace(value=2)], None

    assert [x.value for x in audit.read_pages(pages)] == [1, 2]
    assert len(calls) == 2
    with pytest.raises(audit.AuditError, match="cursor"):
        audit.read_pages(lambda **_: ([], {"PK": "stuck"}))


def test_report_refuses_to_overwrite_prior_evidence(tmp_path: Path):
    target = tmp_path / "report.json"
    audit.write_new_json(target, {"first": True})
    with pytest.raises(FileExistsError):
        audit.write_new_json(target, {"second": True})
    assert audit.load_json(target) == {"first": True}


def test_project_orphans_are_not_reported_as_missing_photos():
    _, _, project = inputs()
    project["images"].append({"image_id": "unfinished"})
    project["summaries"].append({"image_id": "orphan", "receipt_id": 1})
    result = audit.project_integrity(project)
    assert result["images_without_receipts"] == ["unfinished"]
    assert result["summaries_without_receipt"] == [["orphan", 1]]


@pytest.mark.parametrize(
    "field,value", [("asset_key", 1), ("asset_key", " "), ("revision", None)]
)
def test_invalid_source_identity_rejected(field, value):
    library, ledger, project = inputs()
    library["assets"][0][field] = value
    with pytest.raises(audit.AuditError):
        audit.coverage_report(library, ledger, project)


def test_private_output_cannot_enter_checkout(tmp_path: Path):
    (tmp_path / ".git").write_text("gitdir: elsewhere")
    with pytest.raises(audit.AuditError, match="outside Git"):
        audit.write_new_json(tmp_path / "private.json", {"receipt": "private"})
    assert not (tmp_path / "private.json").exists()


def test_report_cli_produces_private_evidence(tmp_path: Path, monkeypatch):
    library, ledger, project = inputs()
    args = ["receipt_photo_audit", "report"]
    for name, value in [
        ("library", library),
        ("ledger", ledger),
        ("project", project),
    ]:
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        args += ["--" + name, str(path)]
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", args + ["--output", str(output)])
    assert audit.main() == 0
    assert audit.load_json(output)["all_receipts_processed"]
    assert audit.main() == 1  # Never overwrite the previous report.


def test_changed_summary_invalidates_prior_qa():
    library, ledger, project = inputs()
    project["summaries"][0]["grand_total"] = 987.65
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"imported_needs_review": 1}
    assert not result["all_receipts_processed"]


def test_new_snapshot_requires_fresh_content_review_with_unchanged_metadata():
    library, ledger, project = inputs()
    project["snapshot_at"] = "2026-09-07T13:00:00+00:00"
    result = audit.coverage_report(library, ledger, project)
    assert result["counts"] == {"imported_needs_review": 1}
    assert not result["all_receipts_processed"]


@pytest.mark.parametrize("timestamp", [None, "", " ", False])
def test_missing_snapshot_time_cannot_bind_qa(timestamp):
    library, ledger, project = inputs()
    project["snapshot_at"] = timestamp
    ledger["photos"][0]["verification"]["checked_snapshot_at"] = timestamp
    assert not audit.coverage_report(library, ledger, project)[
        "all_receipts_processed"
    ]


def test_unrelated_receipt_changes_do_not_invalidate_qa():
    library, ledger, project = inputs()
    project["summaries"].append({"image_id": "other", "receipt_id": 1})
    assert audit.coverage_report(library, ledger, project)[
        "all_receipts_processed"
    ]
