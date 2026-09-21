"""export_merchant provenance wiring (no AWS: exporter + scan are stubbed).

Needs the renderer stack (``export_pipeline_assets`` imports boto3 and
receipt_dynamo); skipped where only the stdlib glyphstudio modules resolve.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
epa = pytest.importorskip("export_pipeline_assets")

from glyphstudio.provenance import (  # noqa: E402
    sha256_file,
    sha256_files,
    sha256_json,
)
from glyphstudio.source_snapshot import (  # noqa: E402
    canvas_height_for_source,
    sha256_bytes,
    write_snapshot,
)
from PIL import Image  # noqa: E402


class _StubExporter:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.s3 = object()
        self.client = SimpleNamespace(
            get_image=lambda _image_id: SimpleNamespace(image_type="SCAN")
        )
        self.payload = {"words": [{"text": "TOTAL"}], "merchant": "Stub"}
        self.faces = {
            "regular": str(tmp_path / "regular.npz"),
            "heavy": str(tmp_path / "heavy.npz"),
        }
        for face, path in self.faces.items():
            with open(path, "wb") as fh:
                fh.write(face.encode())

    def check_receipt(self, merchant, image_id, rid):
        return SimpleNamespace(width=760, height=1200)

    def render_final(
        self,
        merchant,
        image_id,
        rid,
        *,
        width,
        height,
        out_png,
        label_key=None,
    ):
        Image.new("RGB", (width, height), "white").save(out_png)
        labels = {
            "tokens": ["TOTAL"],
            "bboxes": [[0, 0, 10, 10]],
            "ner_tags": ["O"],
            "receipt_key": label_key or f"{image_id}#{rid}",
        }
        return {
            "labels": labels,
            "payload": self.payload,
            "bitmap_font_paths": list(self.faces.values()),
        }


def test_export_merchant_hashes_rendered_faces_and_skips_stale_logo(
    tmp_path, monkeypatch
):
    exporter = _StubExporter(tmp_path)
    monkeypatch.setattr(
        epa, "load_real_scan", lambda _s3, _r: Image.new("RGB", (76, 120))
    )
    monkeypatch.setattr(epa.rsr, "_merchant_logo", lambda _m: None)
    monkeypatch.setattr(epa.pa, "compose_steps", lambda _labels: [])
    monkeypatch.setattr(
        epa, "exporter_commit", lambda _root, allow_dirty=False: "cafebabe"
    )
    out_root = tmp_path / "out"
    os.makedirs(out_root / "stub")
    # a stale logo.png from an earlier run must not become provenance
    (out_root / "stub" / "logo.png").write_bytes(b"stale")
    manifest = tmp_path / "pipeline_merchants.json"
    manifest.write_text(
        json.dumps({"merchants": {"stub": {"merchant": "Stub"}}}),
        encoding="utf-8",
    )
    spec = {
        "merchant": "Stub",
        "font": "nonexistent-font",
        "receipt": {"image_id": "img", "receipt_id": 1},
    }

    summary = epa.export_merchant(
        "stub",
        spec,
        exporter,
        out_root=str(out_root),
        finale_only=True,
        corpus_path=None,
        logo_override=None,
        manifest_path=str(manifest),
    )

    prov = summary["provenance"]
    assert prov["logo_sha256"] is None  # stale logo.png is not provenance
    # the faces render_final actually resolved, not the studio font.json
    assert prov["font_sha256"] == sha256_files(list(exporter.faces.values()))
    assert prov["source_snapshot_sha256"] == sha256_json(exporter.payload)
    assert prov["final_webp_sha256"] == sha256_file(
        str(out_root / "stub" / "final.webp")
    )
    assert prov["exporter_commit"] == "cafebabe"
    assert prov["image_type"] == "SCAN"
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    assert saved["merchants"]["stub"]["provenance"] == prov


def _git(repo, *args, env=None):
    subprocess.check_call(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit_all(repo, message):
    _git(repo, "add", "-A")
    _git(
        repo,
        "commit",
        "-m",
        message,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def _head(repo) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def _two_merchant_manifest(path):
    path.write_text(
        json.dumps(
            {
                "merchants": {
                    "alpha": {
                        "merchant": "Alpha",
                        "font": "missing-font",
                        "receipt": {"image_id": "img-a", "receipt_id": 1},
                    },
                    "beta": {
                        "merchant": "Beta",
                        "font": "missing-font",
                        "receipt": {"image_id": "img-b", "receipt_id": 1},
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def _stub_finale_io(monkeypatch, tmp_path):
    monkeypatch.setattr(
        epa, "Exporter", lambda **_kwargs: _StubExporter(tmp_path)
    )
    monkeypatch.setattr(
        epa, "load_real_scan", lambda _s3, _r: Image.new("RGB", (76, 120))
    )
    monkeypatch.setattr(epa.rsr, "_merchant_logo", lambda _m: None)
    monkeypatch.setattr(epa.pa, "compose_steps", lambda _labels: [])


@pytest.mark.parametrize("selector", [["alpha", "beta"], ["--all"]])
def test_multi_merchant_export_checks_head_once(
    tmp_path, monkeypatch, selector
):
    """Provenance writes must not make the next slug look like a dirty HEAD."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    manifest = repo / "pipeline_merchants.json"
    _two_merchant_manifest(manifest)
    _commit_all(repo, "manifest")
    head = _head(repo)
    monkeypatch.setattr(epa, "_ROOT", str(repo))
    checks = {"n": 0}
    real_commit = epa.exporter_commit

    def _counting(root, allow_dirty=False):
        checks["n"] += 1
        return real_commit(root, allow_dirty=allow_dirty)

    monkeypatch.setattr(epa, "exporter_commit", _counting)
    _stub_finale_io(monkeypatch, tmp_path)
    out = tmp_path / "out"

    rc = epa.main(
        [
            *selector,
            "--out-dir",
            str(out),
            "--manifest",
            str(manifest),
            "--finale-only",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert rc == 0
    assert checks["n"] == 1
    saved = json.loads(manifest.read_text(encoding="utf-8"))
    for slug in ("alpha", "beta"):
        assert (
            saved["merchants"][slug]["provenance"]["exporter_commit"] == head
        )


def test_export_still_refuses_a_dirty_tree_before_any_write(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    manifest = repo / "pipeline_merchants.json"
    _two_merchant_manifest(manifest)
    _commit_all(repo, "manifest")
    (repo / "pipeline_merchants.json").write_text(
        manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(epa, "_ROOT", str(repo))
    _stub_finale_io(monkeypatch, tmp_path)
    before = manifest.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="refusing dirty HEAD"):
        epa.main(
            [
                "--all",
                "--out-dir",
                str(tmp_path / "out"),
                "--manifest",
                str(manifest),
                "--finale-only",
                "--cache-dir",
                str(tmp_path / "cache"),
            ]
        )

    assert manifest.read_text(encoding="utf-8") == before


def _pinned_card(tmp_path, *, image_sha=None, source=(760, 2471)):
    from glyphstudio import source_snapshot as snaps

    width, height = source
    snap = {
        "version": 1,
        "slug": "stub",
        "merchant": "Stub",
        "label_receipt": {"image_id": "label-img", "receipt_id": 2},
        "manifest_receipt": {"image_id": "geo-img", "receipt_id": 1},
        "geometry_receipt": {"image_id": "geo-img", "receipt_id": 1},
        "canvas": {
            "w": 760,
            "h": canvas_height_for_source(width, height),
        },
        "source_size": {"width": width, "height": height},
        "image_sha256": image_sha,
        "image_type": "SCAN",
        "words": [
            {
                "text": "PINNED",
                "line_id": 1,
                "word_id": 1,
                "bbox": [1, 2, 3, 4],
                "labels": ["B-MERCHANT_NAME"],
            }
        ],
        "barcodes": [],
    }
    directory = tmp_path / "snaps"
    write_snapshot(snap, str(directory))
    monkey_dir = directory

    return snaps, monkey_dir, snap


def test_export_uses_pinned_canvas_not_live_receipt_height(
    tmp_path, monkeypatch
):
    snaps, directory, snap = _pinned_card(tmp_path)
    monkeypatch.setattr(snaps, "SNAPSHOT_DIR", str(directory))
    exporter = _StubExporter(tmp_path)
    seen = {}
    original_render = exporter.render_final

    def render_final(
        merchant, image_id, rid, *, width, height, out_png, label_key=None
    ):
        seen["image_id"] = image_id
        seen["rid"] = rid
        seen["height"] = height
        seen["label_key"] = label_key
        return original_render(
            merchant,
            image_id,
            rid,
            width=width,
            height=height,
            out_png=out_png,
            label_key=label_key,
        )

    exporter.check_receipt = lambda merchant, image_id, rid: SimpleNamespace(
        width=794, height=2609, image_id=image_id, receipt_id=rid
    )
    exporter.render_final = render_final
    monkeypatch.setattr(
        epa, "load_real_scan", lambda _s3, _r: Image.new("RGB", (76, 120))
    )
    monkeypatch.setattr(epa.rsr, "_merchant_logo", lambda _m: None)
    monkeypatch.setattr(epa.pa, "compose_steps", lambda _labels: [])
    monkeypatch.setattr(
        epa, "exporter_commit", lambda _root, allow_dirty=False: "abc"
    )
    spec = {
        "merchant": "Stub",
        "font": "missing-font",
        "receipt": {"image_id": "geo-img", "receipt_id": 1},
    }

    summary = epa.export_merchant(
        "stub",
        spec,
        exporter,
        out_root=str(tmp_path / "out"),
        finale_only=True,
        corpus_path=None,
        logo_override=None,
        commit="abc",
    )

    assert summary["dims"] == {"w": 760, "h": snap["canvas"]["h"]}
    assert seen["height"] == snap["canvas"]["h"]
    assert seen["image_id"] == "geo-img"
    assert seen["rid"] == 1
    assert seen["label_key"] == "label-img#2"
    labels = json.loads(
        (tmp_path / "out" / "stub" / "final.labels.json").read_text(
            encoding="utf-8"
        )
    )
    assert labels["receipt_key"] == "label-img#2"


def test_export_refuses_live_scan_that_does_not_match_the_pin(
    tmp_path, monkeypatch
):
    snaps, directory, _snap = _pinned_card(
        tmp_path, image_sha=sha256_bytes(b"pinned-scan")
    )
    monkeypatch.setattr(snaps, "SNAPSHOT_DIR", str(directory))
    exporter = _StubExporter(tmp_path)
    exporter.check_receipt = lambda merchant, image_id, rid: SimpleNamespace(
        width=100, height=100, image_id=image_id, receipt_id=rid
    )
    monkeypatch.setattr(
        epa,
        "load_real_scan_bytes",
        lambda _s3, _r: (b"other-scan", Image.new("RGB", (10, 10))),
    )
    monkeypatch.setattr(epa.rsr, "_merchant_logo", lambda _m: None)
    monkeypatch.setattr(epa.pa, "compose_steps", lambda _labels: [])
    spec = {
        "merchant": "Stub",
        "font": "missing-font",
        "receipt": {"image_id": "geo-img", "receipt_id": 1},
    }
    card = tmp_path / "out" / "stub"
    card.mkdir(parents=True)
    (card / "final.webp").write_bytes(b"prior-finale")
    (card / "real.webp").write_bytes(b"prior-real")

    with pytest.raises(RuntimeError, match="live scan sha256"):
        epa.export_merchant(
            "stub",
            spec,
            exporter,
            out_root=str(tmp_path / "out"),
            finale_only=True,
            corpus_path=None,
            logo_override=None,
            commit="abc",
        )

    assert (card / "final.webp").read_bytes() == b"prior-finale"
    assert (card / "real.webp").read_bytes() == b"prior-real"
    assert not (card / "final.labels.json").exists()
    assert not (card / "compose_steps.json").exists()


def test_export_rejects_a_snapshot_whose_manifest_receipt_changed(
    tmp_path, monkeypatch
):
    snaps, directory, _snap = _pinned_card(tmp_path)
    monkeypatch.setattr(snaps, "SNAPSHOT_DIR", str(directory))
    exporter = _StubExporter(tmp_path)
    called = {"render": 0, "scan": 0}
    original_render = exporter.render_final

    def render_final(*args, **kwargs):
        called["render"] += 1
        return original_render(*args, **kwargs)

    exporter.render_final = render_final
    monkeypatch.setattr(
        epa,
        "load_real_scan",
        lambda _s3, _r: called.__setitem__("scan", called["scan"] + 1),
    )
    spec = {
        "merchant": "Stub",
        "font": "missing-font",
        "receipt": {"image_id": "manifest-img", "receipt_id": 9},
    }
    card = tmp_path / "out" / "stub"
    card.mkdir(parents=True)
    (card / "final.webp").write_bytes(b"prior-finale")

    with pytest.raises(RuntimeError, match="refusing a stale pin"):
        epa.export_merchant(
            "stub",
            spec,
            exporter,
            out_root=str(tmp_path / "out"),
            finale_only=True,
            corpus_path=None,
            logo_override=None,
            commit="abc",
        )

    assert called == {"render": 0, "scan": 0}
    assert (card / "final.webp").read_bytes() == b"prior-finale"


def test_cached_payload_ignores_live_dynamo_and_disk_cache(
    tmp_path, monkeypatch
):
    snaps, directory, _snap = _pinned_card(tmp_path)
    monkeypatch.setattr(snaps, "SNAPSHOT_DIR", str(directory))
    from render_merchant_gold import _cached_payload

    cache = tmp_path / "cache"
    cache.mkdir()
    stale = cache / "payload_ReceiptsTable-dc5be22_geo-img_1.json"
    stale.write_text(
        json.dumps(
            {
                "merchant": "Stub",
                "image_id": "geo-img",
                "receipt_id": 1,
                "width": 794,
                "height": 2609,
                "words": [{"text": "LIVE"}],
                "barcodes": [],
            }
        ),
        encoding="utf-8",
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("live Dynamo payload was read")

    monkeypatch.setattr("render_merchant_gold._load_receipt_payload", _boom)
    doc = _cached_payload(
        str(cache),
        "ReceiptsTable-dc5be22",
        "us-test-1",
        "Stub",
        "label-img",
        2,
    )
    assert doc["words"][0]["text"] == "PINNED"
    assert doc["height"] == 2471
    assert doc["receipt_id"] == 1

    def _live(*_args, **_kwargs):
        return 10, 20, [{"text": "LIVE"}], []

    monkeypatch.setattr("render_merchant_gold._load_receipt_payload", _live)
    other = _cached_payload(
        str(cache),
        "ReceiptsTable-dc5be22",
        "us-test-1",
        "Other",
        "label-img",
        2,
    )
    assert other["words"][0]["text"] == "LIVE"
    assert other["receipt_id"] == 2
