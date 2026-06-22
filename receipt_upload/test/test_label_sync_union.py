"""Unit tests for the conflict-safe cross-env label union (pure functions)."""

from types import SimpleNamespace

from receipt_upload.label_sync.union import find_multivalid, plan_union


def _lb(image_id, rid, ln, wd, label, status="VALID", proposed_by="x"):
    return SimpleNamespace(
        image_id=image_id, receipt_id=rid, line_id=ln, word_id=wd,
        label=label, validation_status=status, label_proposed_by=proposed_by,
    )


def _byword(*labels):
    out = {}
    for lb in labels:
        out.setdefault((lb.image_id, lb.receipt_id, lb.line_id, lb.word_id),
                       []).append(lb)
    return out


def test_adds_valid_label_to_word_with_no_valid_label():
    src = _byword(_lb("a", 1, 2, 3, "TAX", "VALID"))
    dst = _byword(_lb("a", 1, 2, 3, "PRODUCT_NAME", "PENDING"))
    adds = plan_union(src, dst, from_env="dev")
    assert len(adds) == 1 and adds[0].label == "TAX"


def test_never_adds_second_valid_label():
    # target already has a VALID label -> must NOT add a second
    src = _byword(_lb("a", 1, 2, 3, "TAX", "VALID"))
    dst = _byword(_lb("a", 1, 2, 3, "LINE_TOTAL", "VALID"))
    assert plan_union(src, dst, from_env="dev") == []


def test_skips_ambiguous_multivalid_source():
    # source word itself has >1 VALID -> don't propagate (resolve source first)
    src = _byword(
        _lb("a", 1, 2, 3, "UNIT_PRICE", "VALID"),
        _lb("a", 1, 2, 3, "LINE_TOTAL", "VALID"),
    )
    dst = _byword(_lb("a", 1, 2, 3, "PRODUCT_NAME", "PENDING"))
    assert plan_union(src, dst, from_env="dev") == []


def test_skips_when_target_already_has_label():
    src = _byword(_lb("a", 1, 2, 3, "TAX", "VALID"))
    dst = _byword(_lb("a", 1, 2, 3, "TAX", "PENDING"))
    assert plan_union(src, dst, from_env="dev") == []


def test_skips_word_absent_in_target():
    # word not present in target table -> handled by record migration, not union
    src = _byword(_lb("a", 1, 2, 3, "TAX", "VALID"))
    dst = _byword(_lb("b", 9, 9, 9, "MERCHANT_NAME", "VALID"))
    assert plan_union(src, dst, from_env="dev") == []


def test_invalid_source_label_never_propagated():
    src = _byword(_lb("a", 1, 2, 3, "TAX", "INVALID"))
    dst = _byword(_lb("a", 1, 2, 3, "PRODUCT_NAME", "PENDING"))
    assert plan_union(src, dst, from_env="dev") == []


def test_find_multivalid_flags_double_valid():
    bw = _byword(
        _lb("a", 1, 2, 3, "DISCOUNT", "VALID"),
        _lb("a", 1, 2, 3, "PRODUCT_NAME", "VALID"),
        _lb("a", 1, 5, 6, "TAX", "VALID"),  # single valid -> not flagged
    )
    mv = find_multivalid(bw)
    assert len(mv) == 1
    assert mv[0][1] == ["DISCOUNT", "PRODUCT_NAME"]
