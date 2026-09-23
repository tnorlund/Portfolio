"""Offline tests for the stage-S1 adjudicated truth tooling: line keys, the
adjudication template writer, the truth scorer and the proposed S3 gate,
and corpus-dir loading. No AWS, no network.
"""

import json
import os
import random

import pytest

from glyphstudio import label_role_audit as audit
from glyphstudio import stylescan
from glyphstudio.source_snapshot import SNAPSHOT_DIR

UNLABELED = stylescan.LABEL_ROLE_UNLABELED


def _load_snapshot(slug):
    with open(os.path.join(SNAPSHOT_DIR, f"{slug}.json")) as fh:
        return json.load(fh)


def _rec(key, role, merchant="acme", source="snapshot", text=None):
    return {
        "source": source,
        "merchant": merchant,
        "line_key": key,
        "text": key if text is None else text,
        "role": role,
        "note": "",
        "adjudicated_by": "test" if role else None,
        "date": "2026-09-23",
    }


def _row(key, label, regex, verdict="label_wins"):
    return {
        "text": key,
        "label_role": label,
        "regex_role": regex,
        "verdict": verdict,
        "line_key": key,
    }


def _audit(rows, merchant="acme", source="snapshot"):
    a = audit.MerchantAudit(source=source, merchant=merchant)
    a.rows = list(rows)
    a.lines = len(rows)
    return a


# --- line_key -------------------------------------------------------------


def test_snapshot_line_keys_unique_and_rerun_stable():
    snap = audit.tag_snapshot_receipt(_load_snapshot("costco"))
    first = [audit.line_key(ln) for ln in audit.snapshot_lines(snap)]
    again = [
        audit.line_key(ln)
        for ln in audit.snapshot_lines(
            audit.tag_snapshot_receipt(_load_snapshot("costco"))
        )
    ]
    assert first == again
    assert None not in first
    assert len(set(first)) == len(first)
    geo = snap["geometry_receipt"]
    assert first[0].startswith(f"{geo['image_id']}#{geo['receipt_id']}/w:")


def test_line_key_ignores_word_order():
    snap = audit.tag_snapshot_receipt(_load_snapshot("vons"))
    line = max(audit.snapshot_lines(snap), key=len)
    shuffled = list(line)
    random.Random(7).shuffle(shuffled)
    assert shuffled != line
    assert audit.line_key(shuffled) == audit.line_key(line)


def test_line_key_qualifies_word_ids_by_line():
    # word_id restarts on every OCR line: 1.1 and 2.1 are different words.
    a = [{"receipt": "img#1", "line_id": 1, "word_id": 1}]
    b = [{"receipt": "img#1", "line_id": 2, "word_id": 1}]
    assert audit.line_key(a) == "img#1/w:1.1"
    assert audit.line_key(b) == "img#1/w:2.1"
    two = [
        {"receipt": "img#1", "line_id": 10, "word_id": 2},
        {"receipt": "img#1", "line_id": 2, "word_id": 1},
    ]
    assert audit.line_key(two) == "img#1/w:2.1,10.2"


def test_line_key_none_without_receipt_or_ids():
    assert audit.line_key([{"line_id": 1, "word_id": 1}]) is None
    assert audit.line_key([{"receipt": "r#1", "text": "x"}]) is None
    mixed = [
        {"receipt": "r#1", "index": 1},
        {"receipt": "r#2", "index": 2},
    ]
    assert audit.line_key(mixed) is None


def test_overlap_rows_share_keys_with_single_line_rows():
    snap = audit.tag_snapshot_receipt(_load_snapshot("costco"))
    by_line = {audit.line_key(ln) for ln in audit.snapshot_lines(snap)}
    rows = audit.snapshot_rows(snap)
    keys = [audit.line_key(r) for r in rows]
    assert None not in keys and len(set(keys)) == len(keys)
    single = [
        k for r, k in zip(rows, keys) if len({w["line_id"] for w in r}) == 1
    ]
    # A visual row that is exactly one whole OCR line keeps that line's key.
    assert single and any(k in by_line for k in single)


def test_pipeline_line_keys_use_receipt_key_and_indices():
    sources = audit.load_sources(which="pipeline", merchants={"cvs"})
    [(src, slug, lines)] = sources
    keys = [audit.line_key(ln) for ln in lines]
    assert None not in keys and len(set(keys)) == len(keys)
    assert all(
        k.startswith("8de52018-6bc6-40fb-8e4e-a880eda24e75#1/t:") for k in keys
    )
    rerun = audit.load_sources(which="pipeline", merchants={"cvs"})[0][2]
    assert [audit.line_key(ln) for ln in rerun] == keys


def test_truth_source_folds_regroupings():
    assert audit.truth_source("snapshot-rows") == "snapshot"
    assert audit.truth_source("corpus-rows") == "corpus"
    assert audit.truth_source("pipeline") == "pipeline"


# --- template writer ------------------------------------------------------


def _template_audits():
    rows = [
        _row("k1", "item", "other"),
        _row("k2", "header", "other"),
        _row("k3", "item", "other"),
        _row("k4", "item", "item", verdict="agree"),
        _row("k5", "item", "other"),
        _row("k6", "savings", "item", verdict="regex_wins"),
        _row("k7", "savings", "item"),
        _row(None, "item", "other"),
        _row("k8", UNLABELED, "item", verdict="unlabeled"),
    ]
    return [_audit(rows)]


def test_template_orders_by_pattern_frequency_and_caps():
    recs = audit.template_records(_template_audits(), per_pattern=0)["acme"]
    assert [r["line_key"] for r in recs] == [
        "k1",
        "k3",
        "k5",
        "k6",
        "k7",
        "k2",
    ]
    capped = audit.template_records(_template_audits(), per_pattern=1)
    assert [r["line_key"] for r in capped["acme"]] == ["k1", "k6", "k2"]
    first = recs[0]
    assert first["role"] is None and first["adjudicated_by"] is None
    assert list(first)[: len(audit.TRUTH_FIELDS)] == list(audit.TRUTH_FIELDS)
    assert first["pattern"] == "item/other"
    assert first["context"] == {"prev": None, "next": "k2"}
    audit.validate_truth_record(first)


def test_write_templates_preserves_adjudications(tmp_path):
    audits = _template_audits()
    [(path, new, kept)] = audit.write_templates(audits, str(tmp_path), 0)
    assert (new, kept) == (6, 0)
    recs = audit.read_truth_file(path)
    # A human fills k3; a stale record (no longer selected) is also present.
    recs[1].update(role="item", note="n", adjudicated_by="me")
    recs.append(_rec("gone", "footer"))
    with open(path, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    [(_, new, kept)] = audit.write_templates(audits, str(tmp_path), 1)
    after = audit.read_truth_file(path)
    keys = [r["line_key"] for r in after]
    assert keys == ["k1", "k6", "k2", "k3", "k5", "k7", "gone"]
    assert new == 0 and kept == 7
    k3 = after[keys.index("k3")]
    assert (k3["role"], k3["adjudicated_by"]) == ("item", "me")


# --- truth validation -----------------------------------------------------


def test_truth_validation_rejects_bad_records(tmp_path):
    audit.validate_truth_record(_rec("k", None))
    with pytest.raises(ValueError, match="role"):
        audit.validate_truth_record(_rec("k", "other"))
    with pytest.raises(ValueError, match="role"):
        audit.validate_truth_record(_rec("k", UNLABELED))
    with pytest.raises(ValueError, match="source"):
        audit.validate_truth_record(_rec("k", "item", source="snapshot-rows"))
    unsigned = _rec("k", "item")
    unsigned["adjudicated_by"] = None
    with pytest.raises(ValueError, match="adjudicated_by"):
        audit.validate_truth_record(unsigned)
    missing = _rec("k", "item")
    del missing["note"]
    with pytest.raises(ValueError, match="note"):
        audit.validate_truth_record(missing)
    for name in ("a.jsonl", "b.jsonl"):
        (tmp_path / name).write_text(json.dumps(_rec("dup", "item")) + "\n")
    with pytest.raises(ValueError, match="duplicate"):
        audit.load_truth(str(tmp_path))


# --- scorer arithmetic ----------------------------------------------------


def _scoring_fixture():
    """Ten truth lines for acme, one null, one stale-text, one unmatched.

    key  label      regex     truth     label  regex  fallback
    a1   item       other     item      ok     -      ok
    a2   item       other     item      ok     -      ok
    a3   payment    item      item      -      ok     -
    a4   savings    item      item      -      ok     -
    a5   unlabeled  footer    footer    -      ok     ok
    a6   unlabeled  item      summary   -      -      -
    a7   header     footer    footer    -      ok     -
    a8   total_line payment   payment   -      ok     -
    a9   summary    summary   summary   ok     ok     ok
    a10  header     other     header    ok     -      ok
    a11  item       other     null
    """
    rows = [
        _row("a1", "item", "other"),
        _row("a2", "item", "other"),
        _row("a3", "payment", "item"),
        _row("a4", "savings", "item"),
        _row("a5", UNLABELED, "footer", verdict="unlabeled"),
        _row("a6", UNLABELED, "item", verdict="unlabeled"),
        _row("a7", "header", "footer"),
        _row("a8", "total_line", "payment"),
        _row("a9", "summary", "summary", verdict="agree"),
        _row("a10", "header", "other"),
        _row("a11", "item", "other"),
        _row("untruthed", "item", "other"),
    ]
    roles = {
        "a1": "item",
        "a2": "item",
        "a3": "item",
        "a4": "item",
        "a5": "footer",
        "a6": "summary",
        "a7": "footer",
        "a8": "payment",
        "a9": "summary",
        "a10": "header",
        "a11": None,
    }
    truth = {("snapshot", k): _rec(k, r) for k, r in roles.items()}
    truth[("snapshot", "a10")]["text"] = "old OCR text"
    truth[("snapshot", "zz")] = _rec("zz", "item")
    truth[("snapshot", "other-merchant")] = _rec("x", "item", merchant="b")
    return [_audit(rows)], truth


def test_scorer_counts():
    audits, truth = _scoring_fixture()
    [sc], overall, unmatched = audit.score_audits(audits, truth)
    assert (sc.scored, sc.null) == (10, 1)
    assert sc.correct == {"label": 4, "regex": 6, "fallback": 5}
    assert sc.rate("label") == 0.4
    assert sc.rate("regex") == 0.6
    assert sc.rate("fallback") == 0.5
    assert sc.stale_text == ["a10"]
    # Unmatched truth is reported only for merchants that were audited.
    assert unmatched == ["zz"]
    assert overall.scored == 10 and overall.correct == sc.correct


def test_scorer_confusion_per_classifier():
    audits, truth = _scoring_fixture()
    [sc], _, _ = audit.score_audits(audits, truth)
    assert sc.confusion["label"]["item"] == {
        "item": 2,
        "payment": 1,
        "savings": 1,
    }
    assert sc.confusion["regex"]["item"] == {"other": 2, "item": 2}
    assert sc.confusion["fallback"]["footer"] == {"footer": 1, "header": 1}
    assert sc.confusion["fallback"]["summary"] == {"item": 1, "summary": 1}
    assert sc.role_correct("regex") == {
        "item": 2,
        "footer": 2,
        "summary": 1,
        "payment": 1,
        "header": 0,
    }
    for c in audit.CLASSIFIERS:
        assert sum(sum(v.values()) for v in sc.confusion[c].values()) == 10


def test_scorer_ignores_other_merchant_and_source():
    audits, truth = _scoring_fixture()
    pipe = {("pipeline", k[1]): v for k, v in truth.items()}
    [sc], _, _ = audit.score_audits(audits, pipe)
    assert sc.scored == 0
    [sc], _, _ = audit.score_audits(
        [_audit(audits[0].rows, merchant="b")], truth
    )
    assert sc.scored == 0


def test_fallback_role():
    assert audit.fallback_role(UNLABELED, "footer") == "footer"
    assert audit.fallback_role("item", "footer") == "item"


# --- S3 gate --------------------------------------------------------------


def _score(pairs):
    """``pairs`` of (truth, fallback, regex) predictions."""
    sc = audit.TruthScore("acme")
    for truth, fb, rx in pairs:
        sc.add(truth, {"label": fb, "regex": rx, "fallback": fb})
    return sc


def test_gate_threshold_is_the_proposed_constant():
    assert audit.S3_GATE_MAX_ROLE_DEFICIT == 5
    assert "PROPOSED" in audit.S3_GATE_STATUS


def test_gate_pass_on_tie():
    sc = _score([("item", "item", "item"), ("footer", "x", "x")])
    assert sc.gate() == ("PASS", [])


def test_gate_fails_when_fallback_below_regex():
    sc = _score([("item", "x", "item"), ("footer", "footer", "footer")])
    status, reasons = sc.gate()
    assert status == "FAIL" and "1 < regex 2" in reasons[0]


def test_gate_role_deficit_boundary():
    # Label+fallback wins overall (+7 header, -5 footer): deficit 5 passes.
    five = [("footer", "x", "footer")] * 5 + [("header", "header", "x")] * 7
    assert _score(five).gate() == ("PASS", [])
    # Deficit 6 on footer fails even though label+fallback is ahead overall.
    six = [("footer", "x", "footer")] * 6 + [("header", "header", "x")] * 8
    sc = _score(six)
    assert sc.correct["fallback"] > sc.correct["regex"]
    status, reasons = sc.gate()
    assert status == "FAIL"
    assert reasons == ["footer: label+fallback 6 lines worse (> 5)"]


def test_gate_no_truth():
    assert audit.TruthScore("acme").gate()[0] == "NO TRUTH"


def test_score_report_prints_threshold_and_gate(tmp_path):
    audits, truth = _scoring_fixture()
    scores, overall, unmatched = audit.score_audits(audits, truth)
    md, md_path, json_path = audit.write_score_report(
        scores, overall, unmatched, str(tmp_path)
    )
    assert "| acme | 10 | 1 | 4 (40%) | 6 (60%) | 5 (50%) | FAIL |" in md
    assert "max role deficit 5 [PROPOSED" in md
    assert "S3 gate acme: FAIL" in md
    assert "truth \\ fallback" in md
    with open(json_path) as fh:
        data = json.load(fh)
    assert data["gate"]["max_role_deficit"] == 5
    assert data["merchants"][0]["gate"]["status"] == "FAIL"
    assert data["unmatched"] == ["zz"]
    assert os.path.exists(md_path)


# --- corpus-dir loading ---------------------------------------------------


def _write_corpus(root):
    for slug, receipt_ids in (("vons", (2, 3)), ("target", (1,))):
        (root / slug).mkdir()
        base = _load_snapshot(slug)
        for rid in receipt_ids:
            snap = dict(base)
            snap["geometry_receipt"] = {
                "image_id": base["geometry_receipt"]["image_id"],
                "receipt_id": rid,
            }
            (root / slug / f"r{rid}.json").write_text(json.dumps(snap))
    (root / "vons" / "notes.txt").write_text("ignored")
    (root / "stray.json").write_text("{}")


def test_corpus_dir_loading(tmp_path):
    _write_corpus(tmp_path)
    got = audit.load_sources(which="corpus", corpus_dir=str(tmp_path))
    assert [(s, m) for s, m, _ in got] == [
        ("corpus", "target"),
        ("corpus", "vons"),
    ]
    vons_lines = got[1][2]
    single = audit.snapshot_lines(_load_snapshot("vons"))
    assert len(vons_lines) == 2 * len(single)
    keys = [audit.line_key(ln) for ln in vons_lines]
    assert len(set(keys)) == len(keys)
    assert {k.split("/")[0].rsplit("#", 1)[1] for k in keys} == {"2", "3"}
    only = audit.load_sources(
        which="corpus", merchants={"vons"}, corpus_dir=str(tmp_path)
    )
    assert [m for _, m, _ in only] == ["vons"]
    rows = audit.load_sources(
        which="corpus",
        corpus_dir=str(tmp_path),
        snapshot_grouping="overlap",
    )
    assert {s for s, _, _ in rows} == {"corpus-rows"}


def test_default_sources_unchanged_by_corpus_support(tmp_path):
    _write_corpus(tmp_path)
    default = audit.load_sources()
    assert {s for s, _, _ in default} == {"snapshot", "pipeline"}
    with_corpus = audit.load_sources(corpus_dir=str(tmp_path))
    assert with_corpus[: len(default)] == default
    assert {s for s, _, _ in with_corpus[len(default) :]} == {"corpus"}
    assert audit.load_sources(which="corpus") == []


def test_cli_corpus_template_and_score(tmp_path, capsys):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_corpus(corpus)
    truth = tmp_path / "truth"
    argv = [
        "--source",
        "corpus",
        "--corpus-dir",
        str(corpus),
        "--merchant",
        "vons",
    ]
    rc = audit.main(["adjudicate-template", *argv, "--truth-dir", str(truth)])
    assert rc == 0
    recs = audit.read_truth_file(str(truth / "vons.jsonl"))
    assert recs and {r["source"] for r in recs} == {"corpus"}
    recs[0].update(role="header", adjudicated_by="test")
    with open(truth / "vons.jsonl", "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    out_dir = tmp_path / "out"
    rc = audit.main(
        ["score", *argv, "--truth", str(truth), "--out-dir", str(out_dir)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert f"| vons | 1 | {len(recs) - 1} |" in out
    assert (out_dir / "report.md").exists()
    assert (out_dir / "report.json").exists()


# --- committed truth ------------------------------------------------------


def test_committed_truth_is_valid_and_current():
    truth = audit.load_truth(audit.TRUTH_DIR)
    assert truth
    for rec in truth.values():
        assert rec["merchant"] + ".jsonl" in os.listdir(audit.TRUTH_DIR)
    audits = audit.run_audit(audit.load_sources())
    scores, overall, unmatched = audit.score_audits(audits, truth)
    assert unmatched == []
    assert overall.stale_text == []
    assert overall.scored + overall.null == len(truth)
