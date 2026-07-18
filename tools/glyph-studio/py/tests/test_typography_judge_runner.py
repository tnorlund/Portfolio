"""Offline tests for the blind judge runner and Costco gate scorer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import typography_gate_score as tgs
import typography_judge_runner as tjr
from glyphstudio.packets import build_manifest, canonical_json, write_manifest
from PIL import Image

_PID1 = "00000000-0000-4000-8000-000000000001_7_L000"
_PID2 = "00000000-0000-4000-8000-000000000002_8_L001"


def _write_png(path: Path, pixels: list[list[int]]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (len(pixels[0]), len(pixels)))
    image.putdata([value for row in pixels for value in row])
    image.save(path, format="PNG")
    payload = path.read_bytes()
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": image.width,
        "height": image.height,
    }


def _packet(out_dir: Path, packet_id: str, tier: str, slant: float) -> dict:
    image_id, receipt_and_line = packet_id.split("_", 1)
    receipt_id = int(receipt_and_line.split("_", 1)[0])
    line_index = int(packet_id.rsplit("L", 1)[1])
    line_rel = f"packets/{packet_id}/line.png"
    first_rel = f"packets/{packet_id}/letters/000_u0041.png"
    second_rel = f"packets/{packet_id}/letters/001_u0042.png"
    line = _write_png(
        out_dir / line_rel,
        [[255, 0, 255], [0, 0, 0]],
    )
    first = _write_png(out_dir / first_rel, [[0, 255], [0, 0]])
    second = _write_png(out_dir / second_rel, [[255], [0], [255]])
    return {
        "packet_id": packet_id,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_index": line_index,
        "line_ids": [10 + line_index],
        "merchant": "SENTINEL MERCHANT NEVER LEAK",
        "slug": "sentinel-slug-never-leak",
        "text": "AB",
        "features": {
            "cap_px": 12.0,
            "stroke_med": 2.0,
            "density_med": 0.25,
            "n_letters": 2,
            "contamination": 0.0,
            "underline": False,
            "reverse_video": 0,
            "slant_deg": slant,
            "tier": tier,
        },
        "receipt_context": {
            "body_cap_px": 12.0,
            "body_stroke_px": 2.0,
            "image_size": [100, 200],
            "ocr_overlap_score": 0,
        },
        "crops": {
            "line": {"path": line_rel, **line},
            "letters": [
                {
                    "seq": 0,
                    "char": "A",
                    "line_id": 10 + line_index,
                    "path": first_rel,
                    **first,
                },
                {
                    "seq": 1,
                    "char": "B",
                    "line_id": 10 + line_index,
                    "path": second_rel,
                    **second,
                },
            ],
        },
    }


def _manifest(out_dir: Path, packets: list[dict]) -> dict:
    manifest = build_manifest(packets, {"merchants": ["Costco:costco"]})
    write_manifest(manifest, str(out_dir))
    return manifest


def _verdict(
    packet_id: str,
    judge: str,
    content_hash: str,
    *,
    family: str = "mono",
    monospace: bool | None = True,
    italic: bool | None = False,
    weight: str = "normal",
    tier: str = "normal",
    slant: float | None = 0.0,
) -> dict:
    return {
        "schema_version": "tj-out-1",
        "packet_id": packet_id,
        "judge": judge,
        "manifest_content_hash": content_hash,
        "abstain": False,
        "abstain_reason": None,
        "typeface": {
            "family_class": family,
            "monospace": monospace,
            "italic": italic,
            "weight": weight,
            "description": "Dot-matrix receipt face",
        },
        "tier": tier,
        "underline": False,
        "reverse_video": False,
        "slant_deg": slant,
        "confidence": {
            "typeface": 0.9,
            "tier": 0.8,
            "underline": 0.9,
            "reverse_video": 0.95,
            "slant": 0.7,
        },
        "notes": "",
    }


def _args(out_dir: Path, judge: str = "codex") -> SimpleNamespace:
    return SimpleNamespace(
        out_dir=str(out_dir),
        judge=judge,
        packets=None,
        workers=2,
        timeout=5,
        retry=1,
        only_packet_id=None,
    )


def test_json_extraction_from_codex_and_grok_transcripts():
    first = {"ignored": True}
    verdict = {"packet_id": _PID1, "nested": {"value": 1}}
    codex_stdout = (
        "startup noise\n"
        + canonical_json(first)
        + "\ntokens used: 123\n"
        + canonical_json(verdict)
        + "\n"
    )
    assert tjr.extract_verdict("codex", codex_stdout) == verdict

    reply = "analysis before answer\n" + canonical_json(verdict)
    grok_stdout = json.dumps({"text": reply, "usage": {"tokens": 5}})
    assert tjr.extract_verdict("grok", grok_stdout) == verdict
    assert tjr.extract_verdict("codex", "garbage") is None
    assert tjr.extract_verdict("grok", "not JSON") is None


def test_wrong_identity_retries_then_records_runner_abstain(
    tmp_path, monkeypatch
):
    packet = _packet(tmp_path, _PID1, "normal", 0.0)
    manifest = _manifest(tmp_path, [packet])
    wrong = _verdict(
        "00000000-0000-4000-8000-000000000099_7_L000",
        "codex",
        manifest["content_hash"],
    )
    calls = []

    def fake_subprocess(command, timeout):
        calls.append((command, timeout))
        return SimpleNamespace(
            stdout=canonical_json(wrong), stderr="", returncode=0
        )

    monkeypatch.setattr(tjr, "_run_subprocess", fake_subprocess)
    summary = tjr.run(_args(tmp_path))

    assert len(calls) == 2
    assert "previous reply was invalid" in calls[1][0][-1]
    row = json.loads(
        (tmp_path / "judging" / "verdicts_codex.jsonl").read_text()
    )
    assert row["attempts"] == 2
    assert row["runner_abstain"] is True
    assert row["verdict"]["abstain"] is True
    assert row["verdict"]["typeface"] is None
    assert summary["runner_abstains"] == 1


def test_resume_skips_already_judged_packet(tmp_path, monkeypatch):
    packet = _packet(tmp_path, _PID1, "normal", 0.0)
    manifest = _manifest(tmp_path, [packet])
    verdict = _verdict(_PID1, "codex", manifest["content_hash"])
    judging = tmp_path / "judging"
    judging.mkdir()
    row = {
        "packet_id": _PID1,
        "verdict": verdict,
        "runner_abstain": False,
        "attempts": 1,
        "elapsed_s": 0.2,
    }
    (judging / "verdicts_codex.jsonl").write_text(
        canonical_json(row) + "\n", encoding="ascii"
    )

    def unexpected_subprocess(command, timeout):
        raise AssertionError((command, timeout))

    monkeypatch.setattr(tjr, "_run_subprocess", unexpected_subprocess)
    summary = tjr.run(_args(tmp_path))
    assert summary["judged"] == 1
    assert (
        judging / f"verdicts_codex_{manifest['content_hash'][:16]}.json"
    ).exists()


def test_contact_sheet_and_line_upscale_are_deterministic(tmp_path):
    packet = _packet(tmp_path, _PID1, "normal", 0.0)
    blind = tjr.judge_input(packet)
    line_path, sheet_path = tjr.build_judging_artifacts(blind, str(tmp_path))
    first = (Path(line_path).read_bytes(), Path(sheet_path).read_bytes())
    tjr.build_judging_artifacts(blind, str(tmp_path))
    second = (Path(line_path).read_bytes(), Path(sheet_path).read_bytes())
    assert first == second

    with Image.open(line_path) as image:
        assert image.size == (12, 8)
    with Image.open(sheet_path) as image:
        # 2*8 + 1*8 + one fixed 16px gutter, bottom-aligned at 24px.
        assert image.size == (40, 24)
        assert image.getpixel((0, 0)) == 255
        assert image.getpixel((0, 8)) == 0
        assert image.getpixel((20, 12)) == 255


def test_prompt_is_blind_to_packet_merchant_and_slug(tmp_path):
    packet = _packet(tmp_path, _PID1, "normal", 0.0)
    manifest = _manifest(tmp_path, [packet])
    blind = tjr.judge_input(packet)
    line_path, sheet_path = tjr.build_judging_artifacts(blind, str(tmp_path))
    for judge in ("codex", "grok"):
        prompt = tjr.build_prompt(
            judge,
            blind,
            manifest["content_hash"],
            line_path,
            sheet_path,
        )
        assert "SENTINEL MERCHANT NEVER LEAK" not in prompt
        assert "sentinel-slug-never-leak" not in prompt


def test_gate_scorer_metrics_agreement_adjudication_and_failure(tmp_path):
    first = _packet(tmp_path, _PID1, "bold", 0.0)
    second = _packet(tmp_path, _PID2, "normal", 10.0)
    manifest = _manifest(tmp_path, [first, second])
    content_hash = manifest["content_hash"]
    codex = [
        _verdict(
            _PID1,
            "codex",
            content_hash,
            weight="bold",
            tier="bold",
            slant=1.0,
        ),
        _verdict(_PID2, "codex", content_hash, slant=9.0),
    ]
    grok = [
        _verdict(
            _PID1,
            "grok",
            content_hash,
            weight="bold",
            tier="bold",
            slant=0.0,
        ),
        _verdict(
            _PID2,
            "grok",
            content_hash,
            family="sans",
            monospace=False,
            italic=True,
            weight="bold",
            slant=4.0,
        ),
    ]
    judging = tmp_path / "judging"
    judging.mkdir()
    (judging / f"verdicts_codex_{content_hash[:16]}.json").write_text(
        canonical_json(codex) + "\n", encoding="ascii"
    )
    (judging / f"verdicts_grok_{content_hash[:16]}.json").write_text(
        canonical_json(grok) + "\n", encoding="ascii"
    )

    result = tgs.score(str(tmp_path))
    assert result["judges"]["codex"]["monospace_acc"] == 1.0
    assert result["judges"]["codex"]["italic_acc"] == 1.0
    assert result["judges"]["codex"]["family_ok_rate"] == 1.0
    assert result["judges"]["codex"]["slant"]["mean_abs_error"] == 1.0
    assert result["judges"]["grok"]["monospace_acc"] == 0.5
    assert result["judges"]["grok"]["italic_acc"] == 0.5
    assert result["judges"]["grok"]["family_class_dist"]["sans"] == 1
    assert result["judges"]["grok"]["weight_discounted"]["normal_rate"] == 0.0
    assert (
        result["agreement"]["attributes"]["family_class"]["agreement_rate"]
        == 0.5
    )
    assert (
        result["agreement"]["attributes"]["slant_deg"]["agreement_rate"] == 0.5
    )
    assert json.loads((judging / "adjudication_queue.json").read_text()) == [
        _PID2
    ]
    assert result["pass_bars"]["codex"]["judge_pass"] is True
    assert result["pass_bars"]["grok"]["judge_pass"] is False
    assert result["gate_pass"] is False

    adjudication = tmp_path / "adjudication.json"
    adjudication.write_text(
        canonical_json(
            [
                {
                    "packet_id": _PID2,
                    "resolution": {
                        "family_class": "mono",
                        "monospace": True,
                        "italic": False,
                        "weight": "normal",
                        "slant_deg": 10.0,
                    },
                    "adjudicator_note": "Costco atlas calibration",
                }
            ]
        ),
        encoding="ascii",
    )
    adjudicated = tgs.score(str(tmp_path), str(adjudication))
    assert adjudicated["final"]["metrics"]["monospace_acc"] == 1.0
    assert adjudicated["final"]["metrics"]["italic_acc"] == 1.0
    assert adjudicated["final"]["metrics"]["family_ok_rate"] == 1.0
