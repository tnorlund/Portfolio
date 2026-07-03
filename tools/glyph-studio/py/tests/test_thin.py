import numpy as np
from glyphstudio.thin import degree_map, prune_spurs, zhang_suen


def _skeleton_is_thin(skel: np.ndarray) -> bool:
    """No 2x2 block fully inked."""
    s = skel.astype(np.uint8)
    blocks = s[:-1, :-1] + s[:-1, 1:] + s[1:, :-1] + s[1:, 1:]
    return int(blocks.max(initial=0)) < 4


def test_thick_bar_thins_to_line():
    mask = np.zeros((30, 60), dtype=bool)
    mask[12:19, 5:55] = True
    skel = zhang_suen(mask)
    assert skel.any()
    assert _skeleton_is_thin(skel)
    deg = degree_map(skel)
    assert int(((deg == 1) & skel).sum()) == 2  # two endpoints


def test_ring_stays_closed_loop():
    yy, xx = np.mgrid[0:60, 0:60]
    r = np.sqrt((yy - 30) ** 2 + (xx - 30) ** 2)
    mask = (r > 12) & (r < 20)
    skel = zhang_suen(mask)
    assert skel.any()
    assert _skeleton_is_thin(skel)
    deg = degree_map(skel)
    assert int(((deg == 1) & skel).sum()) == 0  # no endpoints on a loop


def test_idempotent():
    mask = np.zeros((30, 60), dtype=bool)
    mask[12:19, 5:55] = True
    once = zhang_suen(mask)
    twice = zhang_suen(once)
    assert np.array_equal(once, twice)


def test_prune_spurs_removes_short_hairs():
    skel = np.zeros((21, 41), dtype=bool)
    skel[10, 2:39] = True          # main stroke
    skel[7:10, 20] = True          # 3-px spur off the middle
    pruned = prune_spurs(skel, min_len=5)
    assert not pruned[7:10, 20].any()
    assert pruned[10, 2:39].all()  # main stroke intact
