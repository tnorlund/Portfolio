"""The costco_gold.py shim must freeze the Costco identity and delegate to
merchant_gold.run unchanged. This guards the committed baseline record's
reproducibility without running the (heavy, OCR/GlyphScore-dependent) full
scorecard: it checks that the shim hands merchant_gold the exact namespace the
generalized instrument expects, with ``tool``/``merchant`` pinned so the
scorecard tags match the committed baseline byte-for-byte.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import costco_gold  # noqa: E402
import merchant_gold  # noqa: E402


def test_shim_freezes_identity_and_delegates(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(merchant_gold, "run", fake_run)
    rc = costco_gold.main([
        "--render", "r.webp", "--real", "real.webp", "--labels", "l.json",
        "--refpack", "p.npz", "--chart", "c.npz", "--config", "cfg.json",
        "--out", "o.json",
    ])
    assert rc == 0
    a = captured["args"]
    # Pinned identity -> baseline tags reproduce exactly.
    assert a.tool == "costco_gold.py"
    assert a.merchant == "costco"
    # Chart is required on the shim (Costco always supplies its specimen chart).
    assert a.chart == "c.npz"
    assert a.render == "r.webp" and a.refpack == "p.npz"


def test_shim_requires_chart():
    # Costco's rc/ce rungs are chart-anchored; the shim must not silently run
    # chart-free (that is merchant_gold's job, not the frozen Costco entry).
    with pytest.raises(SystemExit):
        costco_gold.main([
            "--render", "r.webp", "--real", "real.webp", "--labels", "l.json",
            "--refpack", "p.npz", "--config", "cfg.json",
        ])
