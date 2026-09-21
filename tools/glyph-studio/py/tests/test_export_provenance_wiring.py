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

    def render_final(self, merchant, image_id, rid, *, width, height, out_png):
        Image.new("RGB", (width, height), "white").save(out_png)
        labels = {
            "tokens": ["TOTAL"],
            "bboxes": [[0, 0, 10, 10]],
            "ner_tags": ["O"],
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
