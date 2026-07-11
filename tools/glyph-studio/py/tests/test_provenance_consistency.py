"""Unit tests for the compose provenance-consistency pass.

Covers the three fix areas from adversarial-review findings D1/D5/D6/D12:
  (a) data-driven role cardinality + zone constraints (cashier singleton/header)
  (b) scaffold-artifact scrub (card PAN / REF# / AID / TC regenerated)
  (c) compose->render reconciliation + tax liveness
"""

from glyphstudio.provenance_consistency import (
    ROLE_PATTERNS,
    check_role_constraints,
    check_tax_liveness,
    derive_role_constraints,
    process_candidate,
    reconcile,
    repair_role_constraints,
    scrub_scaffold_artifacts,
    _real_artifact_values,
)


# --------------------------------------------------------------------------
# fixtures: a tiny synthetic corpus in the real-receipt schema
# --------------------------------------------------------------------------
def _corpus():
    # cashier ALWAYS appears once, before the first ITEMS line; one receipt is a
    # section-labeling outlier (ITEMS min line_id below the cashier line).
    base = []
    for i in range(4):
        base.append(
            {
                "image_id": f"r{i}",
                "receipt_id": 1,
                "lines": {
                    "2": "Smith's",
                    "6": "702-220-7763",
                    "7": f"Your cashier was NAME{i}",
                    "10": "MILK 3.99",
                    "11": "BREAD 2.49",
                    "20": "BALANCE 6.48",
                    "21": "CHANGE 0.00",
                    "25": "TOTAL NUMBER OF ITEMS SOLD = 2",
                },
                "sections": [
                    {"section_type": "ITEMS", "line_ids": [10, 11]},
                    {"section_type": "PAYMENT", "line_ids": [20, 21]},
                ],
            }
        )
    # outlier: ITEMS section mislabeled to include line 2, so first_item=2 and
    # the cashier at line 7 appears "after" -- must NOT flip the learned zone.
    base.append(
        {
            "image_id": "outlier",
            "receipt_id": 1,
            "lines": {
                "2": "Smith's",
                "7": "Your cashier was ANGELA",
                "10": "EGGS 4.99",
                "20": "BALANCE 4.99",
            },
            "sections": [{"section_type": "ITEMS", "line_ids": [2, 10]}],
        }
    )
    return base


def _candidate(lines, tokens=None, metadata_extra=None):
    """Build a composed-candidate stub with a preview line set."""
    if tokens is None:
        tokens = []
        for ln in lines:
            tokens.extend(ln["text"].split())
    meta = {"synthetic_receipt_preview": {"lines": lines, "truncated": False}}
    if metadata_extra:
        meta.update(metadata_extra)
    return {
        "candidate_id": "cand-test-1",
        "tokens": tokens,
        "bboxes": [[0, 0, 1, 1]] * len(tokens),
        "ner_tags": ["O"] * len(tokens),
        "metadata": meta,
    }


# --------------------------------------------------------------------------
# (a) role cardinality + zone -- data-driven derivation
# --------------------------------------------------------------------------
def test_derive_cashier_singleton_header_zone():
    cons = derive_role_constraints(_corpus())
    cash = cons["cashier"]
    assert cash.max_count == 1  # learned singleton
    assert cash.zone == "before_first_item"  # learned header zone
    assert cash.n_receipts_present == 5


def test_zone_robust_to_single_outlier():
    # Even with the one mislabeled-ITEMS outlier, cashier zone stays strict.
    cons = derive_role_constraints(_corpus())
    assert cons["cashier"].zone == "before_first_item"


def test_derivation_is_not_hardcoded():
    # A corpus where cashier legitimately appears twice per receipt learns a
    # cap of 2 -- the constraint follows the data, not a Smith's constant.
    corpus = [
        {
            "lines": {
                "1": "Your cashier was A",
                "2": "Your cashier was B",
                "5": "MILK 1.00",
            },
            "sections": [{"section_type": "ITEMS", "line_ids": [5]}],
        }
    ]
    cons = derive_role_constraints(corpus)
    assert cons["cashier"].max_count == 2


def test_cardinality_violation_flags_double_cashier():
    cons = derive_role_constraints(_corpus())
    cand = _candidate(
        [
            {"line_id": 7, "text": "Your cashier was CHEC"},
            {"line_id": 10, "text": "MILK 3.99"},
            {"line_id": 11, "text": "Your cashier was SANJA"},  # mid-items dup
        ]
    )
    viol = check_role_constraints(cand, cons)
    kinds = {(v.role, v.kind) for v in viol}
    assert ("cashier", "cardinality") in kinds
    assert ("cashier", "zone") in kinds


def test_repair_drops_out_of_zone_cashier_keeps_header():
    cons = derive_role_constraints(_corpus())
    cand = _candidate(
        [
            {"line_id": 7, "text": "Your cashier was CHEC"},
            {"line_id": 10, "text": "MILK 3.99"},
            {"line_id": 11, "text": "Your cashier was SANJA"},
        ]
    )
    repaired, actions = repair_role_constraints(cand, cons)
    prev = repaired["metadata"]["synthetic_receipt_preview"]["lines"]
    cashiers = [l for l in prev if "cashier" in l["text"].lower()]
    assert len(cashiers) == 1
    assert cashiers[0]["line_id"] == 7  # kept the header one
    assert "SANJA" not in " ".join(repaired["tokens"])  # dropped from tokens too
    assert len(actions) == 1
    # re-check: repaired candidate has no role violations
    assert check_role_constraints(repaired, cons) == []


def test_single_cashier_no_violation():
    cons = derive_role_constraints(_corpus())
    cand = _candidate(
        [
            {"line_id": 7, "text": "Your cashier was CHEC"},
            {"line_id": 10, "text": "MILK 3.99"},
        ]
    )
    assert check_role_constraints(cand, cons) == []


# --------------------------------------------------------------------------
# (b) scaffold-artifact scrub
# --------------------------------------------------------------------------
def test_real_artifact_values_extracted():
    corpus = [
        {"lines": {"1": "AID: A0000000041010", "2": "TC: 828F76A99385F920",
                   "3": "************2777"}}
    ]
    vals = _real_artifact_values(corpus)
    assert "A0000000041010" in vals
    assert "828F76A99385F920" in vals


def test_scrub_regenerates_card_block():
    real = {"828F76A99385F920", "A0000000041010", "025819"}
    cand = _candidate(
        [{"line_id": 1, "text": "AID: A0000000041010 TC: 828F76A99385F920"}],
        tokens=["MASTERCARD", "*******0658", "REF#:", "025819",
                "AID:", "A0000000041010", "TC:", "828F76A99385F920"],
    )
    scrubbed, rep = scrub_scaffold_artifacts(cand, real)
    toks = scrubbed["tokens"]
    # none of the real verbatim values survive
    assert "828F76A99385F920" not in toks
    assert "A0000000041010" not in toks
    assert "025819" not in toks
    # masked PAN keeps its mask shape, only the tail changes
    pan = next(t for t in toks if t.startswith("*******"))
    assert pan != "*******0658"
    assert pan.startswith("*******") and len(pan) == len("*******0658")
    # TC is still 16 hex chars, AID keeps A + zero-run shape
    tc = toks[toks.index("TC:") + 1]
    assert len(tc) == 16 and all(c in "0123456789ABCDEF" for c in tc)
    aid = toks[toks.index("AID:") + 1]
    assert aid.startswith("A0000000") and aid != "A0000000041010"
    kinds = {r["kind"] for r in rep.replacements}
    assert {"masked_pan", "tc", "aid", "ref"} <= kinds


def test_scrub_is_deterministic():
    real = {"828F76A99385F920"}
    mk = lambda: _candidate([{"line_id": 1, "text": "x"}],
                            tokens=["TC:", "828F76A99385F920"])
    a, _ = scrub_scaffold_artifacts(mk(), real)
    b, _ = scrub_scaffold_artifacts(mk(), real)
    assert a["tokens"] == b["tokens"]  # seeded by candidate_id


def test_scrub_replacement_never_equals_real():
    real = {"1234567890123456"}
    cand = _candidate([{"line_id": 1, "text": "x"}],
                      tokens=["TC:", "1234567890ABCDEF"])
    scrubbed, _ = scrub_scaffold_artifacts(cand, real)
    assert scrubbed["tokens"][1] not in real


# --------------------------------------------------------------------------
# (c) compose->render reconciliation + tax liveness
# --------------------------------------------------------------------------
def test_reconcile_flags_dropped_line():
    # preview has a card-auth line that never appears in the emitted tokens
    cand = _candidate(
        [
            {"line_id": 1, "text": "MILK 3.99"},
            {"line_id": 2, "text": "MASTERCARD Purchase"},  # dropped at render
        ],
        tokens=["MILK", "3.99"],
    )
    rep = reconcile(cand)
    assert not rep.ok
    dropped = {d["line_id"] for d in rep.dropped_lines}
    assert 2 in dropped


def test_reconcile_flags_double_stitch():
    cand = _candidate(
        [{"line_id": 1, "text": "BALANCE 6.99"}],
        tokens=["BALANCE", "6.99", "BALANCE"],  # BALANCE emitted twice
    )
    rep = reconcile(cand)
    dup = {d["token"] for d in rep.duplicated_tokens}
    assert "BALANCE" in dup


def test_reconcile_clean_when_one_to_one():
    cand = _candidate(
        [{"line_id": 1, "text": "MILK 3.99"}, {"line_id": 2, "text": "BREAD 2.49"}],
        tokens=["MILK", "3.99", "BREAD", "2.49"],
    )
    rep = reconcile(cand)
    assert rep.dropped_lines == []
    assert rep.duplicated_tokens == []


def test_tax_liveness_flags_stale_gross():
    cand = _candidate(
        [
            {"line_id": 1, "text": "TAX 5.05"},
            {"line_id": 2, "text": "TAX EXEMPTION 5.05-"},
            {"line_id": 3, "text": "EXEMPTED SALES AMT 6.99"},
        ],
        metadata_extra={
            "arithmetic_reconciliation": {
                "new_tax": "0.00",
                "new_grand_total": "6.99",
                "tax_basis": "non_taxable_observed_catalog",
            }
        },
    )
    issues = {i["kind"] for i in check_tax_liveness(cand)}
    assert "stale_gross_tax" in issues
    assert "orphan_exemption_block" in issues


def test_tax_liveness_ok_when_live():
    cand = _candidate(
        [{"line_id": 1, "text": "TAX 0.00"}],
        metadata_extra={
            "arithmetic_reconciliation": {
                "new_tax": "0.00",
                "new_grand_total": "6.99",
                "tax_basis": "non_taxable_observed_catalog",
            }
        },
    )
    assert check_tax_liveness(cand) == []


def test_exemption_ok_with_marketplace_context():
    cand = _candidate(
        [
            {"line_id": 1, "text": "DOORDASH MARKETPLA"},
            {"line_id": 2, "text": "TAX EXEMPTION 5.05-"},
            {"line_id": 3, "text": "EXEMPTED SALES AMT 60.25"},
        ],
        metadata_extra={
            "arithmetic_reconciliation": {
                "new_tax": "5.05",
                "new_grand_total": "60.25",
                "tax_basis": "marketplace",
            }
        },
    )
    # gross matches reconciled tax and marketplace context present -> no issue
    assert check_tax_liveness(cand) == []


# --------------------------------------------------------------------------
# end-to-end driver
# --------------------------------------------------------------------------
def test_process_candidate_end_to_end():
    cons = derive_role_constraints(_corpus())
    real = {"828F76A99385F920"}
    cand = _candidate(
        [
            {"line_id": 7, "text": "Your cashier was CHEC"},
            {"line_id": 10, "text": "MILK 3.99"},
            {"line_id": 11, "text": "Your cashier was SANJA"},
            {"line_id": 20, "text": "TC: 828F76A99385F920"},
        ],
        tokens=["Your", "cashier", "was", "CHEC", "MILK", "3.99",
                "Your", "cashier", "was", "SANJA", "TC:", "828F76A99385F920"],
    )
    repaired, result = process_candidate(cand, cons, real)
    assert result.rejected  # v1 flagged
    assert result.role_actions  # repaired
    assert "SANJA" not in repaired["tokens"]
    assert "828F76A99385F920" not in repaired["tokens"]
    # repaired candidate is clean on role constraints
    assert check_role_constraints(repaired, cons) == []


def test_all_role_patterns_compile():
    import re

    for p in ROLE_PATTERNS.values():
        re.compile(p)
