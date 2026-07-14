"""Cache behavior of the typography_runs CLI (no network: extractor faked)."""

from __future__ import annotations

import json

import numpy as np
import pytest

import typography_runs as tr


@pytest.fixture
def fake_extract(monkeypatch):
    calls = {"n": 0}

    def _fake(client, merchant, image_id, receipt_id):
        calls["n"] += 1
        meta = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant": merchant,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
            "lines": [{"index": 0, "text": "HI"}],
            "sections": [],
        }
        masks = [np.ones((32, 32), bool)]
        return meta, masks, [0], ["H"], [0.0]

    monkeypatch.setattr(tr, "_extract_receipt", _fake)
    return calls


def test_cache_roundtrip_and_reuse(tmp_path, fake_extract):
    args = (None, "M", "m", "img-1", 1, str(tmp_path))
    meta1, masks1, li1, ch1, sl1 = tr.load_or_extract(*args)
    assert fake_extract["n"] == 1
    assert meta1["cache_schema"] == tr.CACHE_SCHEMA
    # second call must come from disk, byte-identical payload
    meta2, masks2, li2, ch2, sl2 = tr.load_or_extract(*args)
    assert fake_extract["n"] == 1
    assert meta2 == meta1
    assert np.array_equal(np.stack(masks1), masks2)
    assert (li2, ch2, sl2) == ([0], ["H"], [0.0])
    assert not list(tmp_path.glob("*.tmp"))  # atomic publish left no temp


def test_corrupt_cache_is_a_miss(tmp_path, fake_extract):
    args = (None, "M", "m", "img-1", 1, str(tmp_path))
    tr.load_or_extract(*args)
    path = next(tmp_path.glob("*.npz"))
    path.write_bytes(b"truncated garbage")  # simulate a killed writer
    meta, _, _, _, _ = tr.load_or_extract(*args)
    assert fake_extract["n"] == 2
    assert meta["image_id"] == "img-1"


def test_stale_schema_is_a_miss(tmp_path, fake_extract):
    args = (None, "M", "m", "img-1", 1, str(tmp_path))
    tr.load_or_extract(*args)
    path = next(tmp_path.glob("*.npz"))
    d = dict(np.load(path, allow_pickle=False))
    meta = json.loads(str(d["meta"]))
    meta["cache_schema"] = tr.CACHE_SCHEMA - 1
    d["meta"] = json.dumps(meta)
    np.savez_compressed(path, **d)
    tr.load_or_extract(*args)
    assert fake_extract["n"] == 2
