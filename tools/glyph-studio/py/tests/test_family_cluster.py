"""Unit tests for M2 family clustering (synthetic glyphs)."""
import numpy as np
from glyphstudio.family_cluster import (
    normalize_glyph, glyph_iou, merchant_iou, pairwise_iou, cluster_families,
)


def test_normalize_crops_and_resizes():
    b = np.zeros((10, 10), dtype=bool)
    b[4:6, 4:6] = True                 # 2x2 ink block
    n = normalize_glyph(b, size=8)
    assert n.shape == (8, 8) and n.all()   # crop->2x2 all-ink, resize->all-ink


def test_normalize_empty():
    assert not normalize_glyph(np.zeros((5, 5)), size=8).any()


def test_glyph_iou_identical_and_disjoint():
    a = np.zeros((8, 8), dtype=bool); a[:4] = True
    b = np.zeros((8, 8), dtype=bool); b[4:] = True
    assert glyph_iou(a, a) == 1.0
    assert glyph_iou(a, b) == 0.0


def test_merchant_iou_shared_only():
    A = {ord("A"): np.ones((4, 4), bool), ord("B"): np.ones((4, 4), bool)}
    B = {ord("A"): np.ones((4, 4), bool), ord("C"): np.ones((4, 4), bool)}
    iou, n = merchant_iou(A, B, chars="ABC")
    assert n == 1 and iou == 1.0     # only "A" shared


def test_cluster_families_threshold():
    merchants = ["a", "b", "c"]
    # a~b high, c isolated
    iou = np.array([[1, 0.8, 0.4], [0.8, 1, 0.45], [0.4, 0.45, 1]])
    fams = cluster_families(merchants, iou, threshold=0.65)
    assert ["a", "b"] in fams and ["c"] in fams
    assert len(fams) == 2


def test_pairwise_symmetric():
    norm = {
        "x": {ord("A"): np.ones((4, 4), bool)},
        "y": {ord("A"): np.ones((4, 4), bool)},
    }
    m, iou, shared = pairwise_iou(norm, chars="A")
    assert iou[0, 1] == iou[1, 0] == 1.0 and shared[0, 1] == 1


def test_min_shared_gate_blocks_thin_evidence():
    import numpy as np
    from glyphstudio.family_cluster import cluster_families
    merchants = ["a", "b"]
    iou = np.array([[1.0, 0.99], [0.99, 1.0]])   # high IoU...
    shared = np.array([[0, 1], [1, 0]])          # ...but only 1 shared glyph
    fams = cluster_families(merchants, iou, 0.6, shared=shared, min_shared=10)
    assert ["a"] in fams and ["b"] in fams        # not linked on 1 glyph
    fams2 = cluster_families(merchants, iou, 0.6, shared=None)
    assert ["a", "b"] in fams2                     # linked when ungated
