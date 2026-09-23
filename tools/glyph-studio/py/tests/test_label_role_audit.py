"""Offline tests for the label-driven section roles + regex agreement audit.

No AWS, no network: the audit reads committed source snapshots and the
pipeline ``final.labels.json`` files only.
"""

import inspect
import json
import os

import pytest
from glyphstudio import label_role_audit as audit
from glyphstudio import stylescan
from glyphstudio.source_snapshot import SNAPSHOT_DIR

from receipt_dynamo.constants import CORE_LABELS

UNLABELED = stylescan.LABEL_ROLE_UNLABELED


def _w(text, *labels, bbox=None):
    word = {"text": text, "labels": list(labels)}
    if bbox is not None:
        word["bbox"] = bbox
    return word


# --- role table -----------------------------------------------------------

EPIC_TABLE = {
    "total_line": {"GRAND_TOTAL"},
    "summary": {"SUBTOTAL", "TAX", "CHANGE", "CASH_BACK"},
    "payment": {"PAYMENT_METHOD"},
    "item": {"PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"},
    "header": {
        "MERCHANT_NAME",
        "ADDRESS_LINE",
        "PHONE_NUMBER",
        "WEBSITE",
        "STORE_HOURS",
    },
    "savings": {"DISCOUNT", "COUPON", "LOYALTY_ID"},
}


def test_role_table_matches_epic():
    got: dict[str, set[str]] = {}
    for label, role in stylescan.CORE_LABEL_ROLE.items():
        got.setdefault(role, set()).add(label)
    assert got == EPIC_TABLE


def test_role_table_keys_are_core_labels():
    assert set(stylescan.CORE_LABEL_ROLE) <= set(CORE_LABELS)
    # Everything CORE_LABELS has that the table skips is deliberate.
    skipped = set(CORE_LABELS) - set(stylescan.CORE_LABEL_ROLE)
    assert skipped == {"DATE", "TIME", "TIP", "REFUND"}


def test_priority_covers_every_role():
    assert set(stylescan.LABEL_ROLE_PRIORITY) == set(EPIC_TABLE)
    assert UNLABELED not in stylescan.LABEL_ROLE_PRIORITY


def test_classifier_is_merchant_invariant():
    params = inspect.signature(stylescan._classify_from_labels).parameters
    assert list(params) == ["line_words"]


@pytest.mark.parametrize(
    "label,role",
    sorted(stylescan.CORE_LABEL_ROLE.items()),
)
def test_single_label_line_maps_to_its_role(label, role):
    assert stylescan._classify_from_labels([_w("x", label)]) == role


# --- line aggregation -----------------------------------------------------


def test_unlabeled_sentinel():
    assert stylescan._classify_from_labels([]) == UNLABELED
    assert stylescan._classify_from_labels([_w("THANK"), _w("YOU")]) == (
        UNLABELED
    )
    assert stylescan._classify_from_labels([_w("x", "O")]) == UNLABELED
    # Role-less labels (DATE/TIME) and off-taxonomy tags stay unlabeled.
    line = [_w("01/02/25", "DATE"), _w("10:15", "TIME"), _w("x", "BOGUS")]
    assert stylescan._classify_from_labels(line) == UNLABELED


def test_majority_vote_wins():
    line = [
        _w("ORG", "PRODUCT_NAME"),
        _w("EGGS", "PRODUCT_NAME"),
        _w("E", "PAYMENT_METHOD"),
        _w("4.99", "LINE_TOTAL"),
    ]
    assert stylescan._classify_from_labels(line) == "item"


def test_tie_break_prefers_tender_over_total():
    line = [_w("DEBIT", "PAYMENT_METHOD"), _w("$13.38", "GRAND_TOTAL")]
    assert stylescan._classify_from_labels(line) == "payment"


def test_tie_break_prefers_summary_over_payment():
    line = [_w("A", "PAYMENT_METHOD"), _w("14.83", "TAX")]
    assert stylescan._classify_from_labels(line) == "summary"


def test_word_with_two_labels_votes_each_role_once():
    line = [_w("SAVE", "DISCOUNT", "COUPON"), _w("MILK", "PRODUCT_NAME")]
    assert stylescan.label_role_votes(line) == {"savings": 1, "item": 1}


def test_bio_tags_and_single_tag_words():
    line = [
        {"text": "TOTAL", "ner_tag": "O"},
        {"text": "9.99", "ner_tag": "B-GRAND_TOTAL"},
        {"text": "x", "label": "I-grand_total"},
    ]
    assert stylescan._classify_from_labels(line) == "total_line"


# --- line grouping --------------------------------------------------------


def test_snapshot_lines_group_by_line_id():
    snap = {
        "words": [
            {"text": "B", "line_id": 2, "word_id": 2, "labels": []},
            {"text": "Z", "line_id": 1, "word_id": 1, "labels": []},
            {"text": "A", "line_id": 2, "word_id": 1, "labels": []},
        ]
    }
    lines = audit.snapshot_lines(snap)
    assert [[w["text"] for w in ln] for ln in lines] == [["Z"], ["A", "B"]]


def test_overlap_grouping_joins_late_price_tokens():
    # y-up boxes (LayoutLM style): item row at y~800, total row at y~200;
    # the prices arrive at the END of the token list, as in the Costco
    # pipeline file, and must rejoin their rows.
    tokens = ["MILK", "SUBTOTAL", "4.99", "9.99"]
    bboxes = [
        [100, 800, 300, 812],
        [100, 200, 300, 212],
        [800, 801, 900, 813],
        [800, 199, 900, 211],
    ]
    tags = ["B-PRODUCT_NAME", "O", "B-LINE_TOTAL", "B-SUBTOTAL"]
    lines = audit.group_tokens_by_overlap(tokens, bboxes, tags)
    assert [[w["text"] for w in ln] for ln in lines] == [
        ["MILK", "4.99"],
        ["SUBTOTAL", "9.99"],
    ]
    assert lines[0][1]["labels"] == ["LINE_TOTAL"]
    assert lines[1][0]["labels"] == []


def test_overlap_grouping_y_down_and_left_to_right():
    tokens = ["4.99", "EGGS", "TAX"]
    bboxes = [[800, 10, 900, 30], [100, 12, 300, 32], [100, 40, 200, 60]]
    lines = audit.group_tokens_by_overlap(tokens, bboxes, ["O"] * 3)
    assert [[w["text"] for w in ln] for ln in lines] == [
        ["EGGS", "4.99"],
        ["TAX"],
    ]


def test_overlap_grouping_separates_adjacent_rows():
    # Two rows whose boxes touch but overlap < half a line height.
    bboxes = [[0, 100, 50, 112], [0, 110, 50, 122]]
    lines = audit.group_tokens_by_overlap(["a", "b"], bboxes, ["O", "O"])
    assert len(lines) == 2


def test_overlap_grouping_rejects_ragged_input():
    with pytest.raises(ValueError):
        audit.group_tokens_by_overlap(["a"], [], ["O"])


# --- verdicts -------------------------------------------------------------


def test_regex_role_projection():
    assert audit.regex_role("warehouse_header") == "header"
    assert audit.regex_role("address") == "header"
    assert audit.regex_role("savings") == "savings"
    assert audit.regex_role("discount") == "savings"
    assert audit.regex_role("item") == "item"
    assert audit.regex_role("barcode_caption") == "barcode"
    assert audit.regex_role("separator") == "separator"
    assert audit.regex_role("other") == "other"
    assert audit.regex_role("no_such_rule") == "other"


def test_verdict_rules():
    assert audit.verdict(UNLABELED, "item", {}) == "unlabeled"
    assert audit.verdict("item", "item", {"item": 2}) == "agree"
    assert audit.verdict("item", "other", {"item": 2}) == "label_wins"
    assert audit.verdict("header", "footer", {"header": 1}) == "regex_wins"
    votes = {"total_line": 1, "payment": 1}
    assert audit.verdict("total_line", "payment", votes) == "regex_wins"
    assert audit.verdict("savings", "item", {"savings": 2}) == "label_wins"


# --- audit on committed data ----------------------------------------------


def _load_snapshot(slug):
    with open(os.path.join(SNAPSHOT_DIR, f"{slug}.json")) as fh:
        return json.load(fh)


def test_audit_costco_snapshot():
    snap = _load_snapshot("costco")
    lines = audit.snapshot_lines(snap)
    result = audit.audit_lines("snapshot", "costco", lines)
    assert result.lines == len({w["line_id"] for w in snap["words"]})
    assert sum(result.counts.values()) == result.lines
    assert sum(sum(c.values()) for c in result.confusion.values()) == (
        result.lines
    )
    assert len(result.disagreements) == (
        result.counts["label_wins"] + result.counts["regex_wins"]
    )
    assert result.counts["agree"] > 0
    assert result.counts["unlabeled"] > 0
    by_text = {
        " ".join(w["text"] for w in ln): audit.classify_line(ln, "costco")
        for ln in lines
    }
    savings = by_text["INSTANT SAVINGS"]
    assert savings["label_role"] == savings["regex_role"] == "savings"
    address = by_text["5700 Lindero Canyon Rd"]
    assert address["label_role"] == "header"
    assert address["verdict"] == "label_wins"  # Costco rules have no address


def test_costco_rows_do_not_reproduce_1214():
    """#1214: the Costco savings rule swallowing item rows. On the committed
    INSTANT-SAVINGS receipt no item-labeled row is regex-classified savings
    under either grouping."""
    snap = _load_snapshot("costco")
    for lines in (audit.snapshot_lines(snap), audit.snapshot_rows(snap)):
        for ln in lines:
            row = audit.classify_line(ln, "costco")
            if row["label_role"] == "item":
                assert row["regex_role"] != "savings", row["text"]


def test_snapshot_rows_merge_split_price_lines():
    snap = _load_snapshot("costco")
    rows = audit.snapshot_rows(snap)
    assert len(rows) < len(audit.snapshot_lines(snap))
    texts = [" ".join(w["text"] for w in ln) for ln in rows]
    assert any("WATERMELON" in t and "6.99" in t for t in texts)


def test_load_sources_and_report(tmp_path):
    sources = audit.load_sources(merchants={"costco"})
    assert {(s, m) for s, m, _ in sources} == {
        ("snapshot", "costco"),
        ("pipeline", "costco"),
    }
    audits = audit.run_audit(sources)
    md, md_path, json_path = audit.write_report(audits, str(tmp_path), 3)
    assert "| snapshot | costco |" in md
    assert "| pipeline | costco |" in md
    assert "label \\ regex" in md
    with open(json_path) as fh:
        data = json.load(fh)
    assert [d["source"] for d in data] == ["snapshot", "pipeline"]
    assert os.path.exists(md_path)


def test_sample_disagreements_round_robins_patterns():
    rows = [{"label_role": "item", "regex_role": "other"}] * 5 + [
        {"label_role": "header", "regex_role": "other"}
    ]
    picked = audit.sample_disagreements(rows, 2)
    assert {r["label_role"] for r in picked} == {"item", "header"}


def test_cli_writes_report(tmp_path, capsys):
    rc = audit.main(
        [
            "--source",
            "snapshot",
            "--merchant",
            "vons",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "| snapshot | vons |" in out
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.json").exists()
