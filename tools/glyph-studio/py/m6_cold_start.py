#!/usr/bin/env python3
"""M6 cold-start pilot: bootstrap a NOVEL Smith's receipt from scratch.

Smith's has no minted atlas, no typography profile (only a legacy hand-tuned
VT323 entry, which this pilot deliberately ignores), and no stylemap. The
epic's M6 recipe, end to end, with zero hand-drawn glyphs and zero hand-tuned
constants -- every knob is measured or derived:

  vet     inventory the dev corpus; keep receipts with ocr_overlap_score <= 2
          (the m3_acceptance vetting rule) -> vetted_keys.json
  mint    (run build_merchant_glyphs.py with GLYPH_KEYS_JSON=vetted_keys.json;
          this harness does not fork the mint)
  gates   anti-copy check (npz-level byte-identity vs the fleet) + the fleet
          identity gate (glyph_gate.audit_normalized) + family affinity
          (family_cluster.merchant_iou vs every fleet atlas)
  knobs   v1 calibrate_merchant on the vetted corpus (cheap-space
          ocr_cap_height_ratio / bitmap_thin / projections)
  faces   face priors per canonical section from QA'd VALID ReceiptSection
          rows: stylescan's measurement primitives per OCR line, joined to
          sections by line_id (face-map v2 approach), normalized against the
          receipt's own ITEMS body; n>=3 cells else LOW-CONF
  borrow  labeled borrow-variant atlas: native glyphs kept, top-affinity
          fleet merchant fills MISSING chars; gate-flagged MISRENDER natives
          are also replaced (a fleet-gate rule, not an eyeball call); emits a
          per-glyph native/borrowed provenance JSON
  score   production-scorecard ink metrics (_ink_metrics) of a rendered synth
          vs the vetted real corpus's distribution

Rendering itself follows the established compose pipeline (scripts/
compose_synthetic_receipts.py generate + _render_cached_hybrid with a
runtime-injected scratch profile; RECEIPT_PAPER_STRENGTH=0.3). See
../M6_FINDINGS.md for the pilot's numbers and verdict.

Usage:
  python m6_cold_start.py vet    --workdir /private/tmp/m6_work
  python m6_cold_start.py gates  --workdir ... --atlas .../smiths.glyphs.npz
  python m6_cold_start.py knobs  --workdir ... --atlas .../smiths.glyphs.npz
  python m6_cold_start.py faces  --workdir ...
  python m6_cold_start.py borrow --workdir ... --atlas ... --donor vons
  python m6_cold_start.py score  --workdir ... --synth out.png --composed c.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from statistics import median

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "synthesis_loop"),
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_upload"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MERCHANT_VARIANTS = [
    "Smith's",
    "Smith's Marketplace",
    "Smith's Fresh for Everyone",
    "Smiths",
]
FLEET_ATLASES = {
    "costco": "bitMatrix-C2.glyphs.npz",
    "cvs": "cvs.glyphs.npz",
    "homedepot": "homedepot.glyphs.npz",
    "innout": "innout.glyphs.npz",
    "sprouts": "sprouts.glyphs.npz",
    "target": "target.glyphs.npz",
    "traderjoes": "traderjoes.glyphs.npz",
    "vons": "vons.glyphs.npz",
    "wildfork": "wildfork.glyphs.npz",
}
ALL94 = [chr(c) for c in range(33, 127)]
MAX_OVERLAPS = 2  # m3_acceptance vetting rule


def _client():
    from receipt_dynamo.data.dynamo_client import DynamoClient

    return DynamoClient(
        os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
    )


def _is_valid(status: str | None) -> bool:
    s = str(status or "")
    return "VALID" in s and "INVALID" not in s


# --------------------------------------------------------------------------
# vet: inventory + OCR vetting + section coverage
# --------------------------------------------------------------------------
def cmd_vet(args) -> int:
    from glyph_review import _ink_metrics
    from m3_acceptance import ocr_overlap_score
    from receipt_line_scorecard import _load_words_and_real

    client = _client()
    seen, rows = set(), []
    for variant in MERCHANT_VARIANTS:
        places, _ = client.get_receipt_places_by_merchant(variant)
        for p in places:
            key = f"{p.image_id}#{p.receipt_id}"
            if key in seen:
                continue
            seen.add(key)
            row = {
                "key": key,
                "image_id": str(p.image_id),
                "receipt_id": int(p.receipt_id),
                "merchant": p.merchant_name,
            }
            try:
                d = client.get_image_details(p.image_id)
                row["n_letters"] = sum(
                    1
                    for lt in d.receipt_letters
                    if lt.receipt_id == p.receipt_id
                )
                secs = client.get_receipt_sections_from_receipt(
                    str(p.image_id), int(p.receipt_id)
                )
                row["sections"] = [
                    {
                        "type": str(s.section_type),
                        "status": str(getattr(s, "validation_status", "")),
                        "line_ids": list(s.line_ids or []),
                    }
                    for s in secs
                ]
                real, words = _load_words_and_real(
                    p.merchant_name, str(p.image_id), int(p.receipt_id)
                )
                row["n_words"] = len(words)
                row["n_lines_ocr"] = len({w.get("line_id") for w in words})
                row["overlap_score"] = ocr_overlap_score(words)
                row["ink"] = {
                    k: round(float(v), 4)
                    for k, v in (_ink_metrics(real, words) or {}).items()
                }
                row["img_size"] = list(real.size)
            except Exception as exc:  # noqa: BLE001 - stale place rows exist
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            print(
                f"  {key[:14]} letters={row.get('n_letters')} "
                f"overlap={row.get('overlap_score')} "
                f"err={row.get('error')}",
                flush=True,
            )
    vetted = [
        r
        for r in rows
        if not r.get("error")
        and r.get("overlap_score") is not None
        and r["overlap_score"] <= MAX_OVERLAPS
    ]
    n_lines = sum(r.get("n_lines_ocr", 0) for r in vetted)
    n_valid_lines = sum(
        len(s["line_ids"])
        for r in vetted
        for s in r.get("sections", [])
        if _is_valid(s["status"])
    )
    print(
        f"\nreceipts={len(rows)} vetted(overlap<={MAX_OVERLAPS})={len(vetted)}"
        f" letter_crops(vetted)={sum(r.get('n_letters', 0) for r in vetted)}"
        f" VALID-section line coverage={n_valid_lines}/{n_lines}"
        f" ({n_valid_lines / max(1, n_lines):.0%})"
    )
    os.makedirs(args.workdir, exist_ok=True)
    json.dump(
        {"rows": rows},
        open(os.path.join(args.workdir, "inventory.json"), "w"),
        indent=1,
    )
    json.dump(
        [[r["image_id"], r["receipt_id"]] for r in vetted],
        open(os.path.join(args.workdir, "vetted_keys.json"), "w"),
    )
    return 0


# --------------------------------------------------------------------------
# gates: anti-copy + fleet identity gate + family affinity
# --------------------------------------------------------------------------
def _load_npz_raw(path):
    z = np.load(path)
    return {
        int(k[1:]): z[k].astype(bool) for k in z.files if k.startswith("c")
    }


def _load_npz_norm(path, chars):
    from glyphstudio.family_cluster import normalize_glyph

    wanted = {ord(c) for c in chars}
    return {
        cp: normalize_glyph(b, size=32)
        for cp, b in _load_npz_raw(path).items()
        if cp in wanted
    }


def cmd_gates(args) -> int:
    from glyphstudio.family_cluster import merchant_iou
    from glyphstudio.glyph_gate import GATE_CHARS, audit_normalized

    fleet = {
        name: os.path.join(args.bitmatrix, fname)
        for name, fname in FLEET_ATLASES.items()
        if os.path.exists(os.path.join(args.bitmatrix, fname))
    }
    mine_raw = _load_npz_raw(args.atlas)

    # Anti-copy (npz-level): the publisher's gate compares stroke-source
    # geometry, which a crop-minted atlas does not have; the same policy at
    # bitmap level -- substantial glyphs byte-identical to a sibling.
    substantial = {cp: b for cp, b in mine_raw.items() if b.sum() >= 30}
    anti_copy_ok = True
    for name, path in fleet.items():
        theirs = _load_npz_raw(path)
        same = [
            chr(cp)
            for cp, b in substantial.items()
            if cp in theirs
            and theirs[cp].shape == b.shape
            and np.array_equal(theirs[cp], b)
        ]
        if len(same) > max(4, int(0.25 * len(substantial))):
            anti_copy_ok = False
            print(f"anti-copy FAIL vs {name}: {''.join(same)}")
    print(
        f"anti-copy gate: "
        f"{'PASS' if anti_copy_ok else 'FAIL'} "
        f"({len(substantial)} substantial glyphs vs {len(fleet)} siblings)"
    )

    normalized = {
        name: _load_npz_norm(path, GATE_CHARS) for name, path in fleet.items()
    }
    normalized["smiths"] = _load_npz_norm(args.atlas, GATE_CHARS)
    findings = [
        f for f in audit_normalized(normalized) if f.merchant == "smiths"
    ]
    by_kind: dict[str, list] = {}
    for f in findings:
        by_kind.setdefault(f.kind, []).append(f)
    print(f"fleet identity gate: {len(findings)} findings")
    for kind, fs in by_kind.items():
        print(f"  {kind}: {len(fs)}")
        for f in sorted(fs, key=lambda f: -f.score)[: args.top]:
            print(f"    {f.char!r} score={f.score:.3f} {f.detail}")

    have = {chr(cp) for cp in mine_raw}
    missing = [c for c in ALL94 if c not in have]
    print(f"coverage: {len(have)}/94 native; missing: {''.join(missing)}")

    chars = "".join(sorted(have))
    mine_norm = _load_npz_norm(args.atlas, chars)
    affinity = sorted(
        (
            (
                *merchant_iou(_load_npz_norm(path, chars), mine_norm, chars)[
                    ::-1
                ],
                name,
            )
            for name, path in fleet.items()
        ),
        key=lambda t: -t[1],
    )
    print("family affinity (mean IoU over shared glyphs):")
    for n, iou, name in affinity:
        print(f"  {name:12s} {iou:.3f} (n={n})")
    json.dump(
        {
            "anti_copy_pass": anti_copy_ok,
            "findings": [
                {
                    "char": f.char,
                    "kind": f.kind,
                    "score": f.score,
                    "detail": f.detail,
                }
                for f in findings
            ],
            "native": sorted(have),
            "missing": missing,
            "family_affinity": [
                {"merchant": name, "iou": iou, "n_shared": n}
                for n, iou, name in affinity
            ],
        },
        open(os.path.join(args.workdir, "gates.json"), "w"),
        indent=1,
    )
    return 0


# --------------------------------------------------------------------------
# knobs: v1 calibrate_merchant over the vetted corpus
# --------------------------------------------------------------------------
def cmd_knobs(args) -> int:
    from glyph_review import _ink_metrics
    from m3_acceptance import ocr_overlap_score, solve
    from receipt_line_scorecard import _load_words_and_real

    keys = json.load(open(os.path.join(args.workdir, "vetted_keys.json")))
    receipts, h_meds, d_meds, word_h = [], [], [], []
    for iid, rid in keys:
        real, words = _load_words_and_real("Smith's", iid, rid)
        if ocr_overlap_score(words) > MAX_OVERLAPS:
            continue
        m = _ink_metrics(real, words)
        if not m:
            continue
        h_meds.append(m["h_med"])
        d_meds.append(m["density_med"])
        for w in words:
            bb = w.get("bbox") or ()
            if len(bb) == 4:
                word_h.append(abs(bb[3] - bb[1]) / 1000.0 * real.size[1])
        receipts.append(
            {"words": [{"text": w.get("text", "")} for w in words]}
        )
    g = {
        "receipts": receipts,
        "real_cap_height_px": float(np.median(h_meds)),
        "target_density": float(np.median(d_meds)),
        "median_ocr_word_height_px": float(np.median(word_h)),
        "n": len(receipts),
    }
    print(
        f"real-side (n={g['n']}): cap={g['real_cap_height_px']:.1f}px "
        f"word_h={g['median_ocr_word_height_px']:.1f}px "
        f"target_density={g['target_density']:.4f}"
    )
    thin, proj, cov, railed = solve(args.atlas, g)
    print(
        f"derived: bitmap_thin={thin} [{railed}] "
        f"projected={ {k: round(v, 3) for k, v in proj.items()} } "
        f"coverage={cov and round(cov, 3)}"
    )
    json.dump(
        {
            "real_side": {k: v for k, v in g.items() if k != "receipts"},
            "bitmap_thin": thin,
            "railed": railed,
            "projected": proj,
            "coverage": cov,
        },
        open(os.path.join(args.workdir, "knobs.json"), "w"),
        indent=1,
    )
    return 0


# --------------------------------------------------------------------------
# faces: per-section priors from QA'd VALID ReceiptSection rows
# --------------------------------------------------------------------------
def cmd_faces(args) -> int:
    from glyphstudio.stylescan import _run_widths, _sauvola
    from receipt_line_scorecard import _load_words_and_real

    client = _client()
    keys = json.load(open(os.path.join(args.workdir, "vetted_keys.json")))
    inv = json.load(open(os.path.join(args.workdir, "inventory.json")))
    secs_by_key = {r["key"]: r.get("sections", []) for r in inv["rows"]}
    all_lines = []
    for iid, rid in keys:
        key = f"{iid}#{rid}"
        line2sec = {
            int(lid): s["type"]
            for s in secs_by_key.get(key, [])
            if _is_valid(s["status"])
            for lid in s["line_ids"]
        }
        if not line2sec:
            continue
        real, words = _load_words_and_real("Smith's", iid, rid)
        gray = np.asarray(real.convert("L"))
        H, W = gray.shape
        d = client.get_image_details(iid)
        letters_by_line: dict[int, list] = {}
        for lt in d.receipt_letters:
            if lt.receipt_id == rid:
                letters_by_line.setdefault(int(lt.line_id), []).append(lt)
        words_by_line: dict[int, list] = {}
        for w in words:
            if w.get("line_id") is not None:
                words_by_line.setdefault(int(w["line_id"]), []).append(w)
        for lid, sec in sorted(line2sec.items()):
            lts, wds = letters_by_line.get(lid), words_by_line.get(lid)
            if not lts or not wds:
                continue
            lb = max(
                (1 - min(w["bbox"][1], w["bbox"][3]) / 1000) * H for w in wds
            )
            lt_ = min(
                (1 - max(w["bbox"][1], w["bbox"][3]) / 1000) * H for w in wds
            )
            ll = min(min(w["bbox"][0], w["bbox"][2]) / 1000 * W for w in wds)
            lr = max(max(w["bbox"][0], w["bbox"][2]) / 1000 * W for w in wds)
            dens, strokes, caps = [], [], []
            for letter in lts:
                tl, br = letter.top_left, letter.bottom_right
                x0 = min(tl["x"], br["x"]) * W
                x1 = max(tl["x"], br["x"]) * W
                y0 = (1 - max(tl["y"], br["y"])) * H
                y1 = (1 - min(tl["y"], br["y"])) * H
                xi0, yi0 = max(0, int(x0)), max(0, int(y0))
                xi1, yi1 = min(W, int(x1) + 1), min(H, int(y1) + 1)
                if xi1 - xi0 < 2 or yi1 - yi0 < 2:
                    continue
                mask = _sauvola(gray[yi0:yi1, xi0:xi1])
                if mask.sum() < 4:
                    continue
                ch = str(letter.text or "")[:1]
                dens.append(float(mask.mean()))
                runs = _run_widths(mask)
                if runs:
                    strokes.append(float(np.mean(runs)))
                if ch.isupper() or ch.isdigit():
                    caps.append(float(yi1 - yi0))
            underline = False
            line_h = lb - lt_
            yb0 = max(0, int(lb - 0.30 * line_h))
            yb1 = min(H, int(lb + 0.50 * line_h))
            xb0, xb1 = max(0, int(ll) - 3), min(W, int(lr) + 3)
            if yb1 - yb0 >= 2 and xb1 - xb0 > 20:
                region = gray[yb0:yb1, xb0:xb1].astype(float)
                paper = float(np.percentile(region, 90))
                thresh = max(0.0, min(230.0, paper - 25.0))
                need = 0.55 * (xb1 - xb0)
                for rrow in region:
                    ink = rrow < thresh
                    padded = np.concatenate([[0], ink.view(np.uint8), [0]])
                    starts = np.where(np.diff(padded) == 1)[0]
                    ends = np.where(np.diff(padded) == -1)[0]
                    if any(e - s >= need for s, e in zip(starts, ends)):
                        underline = True
                        break
            all_lines.append(
                {
                    "key": key,
                    "section": sec,
                    "cap_px": median(caps) if caps else None,
                    "stroke_med": median(strokes) if strokes else None,
                    "underline": underline,
                }
            )
        print(f"  measured {key}", flush=True)

    by_receipt: dict[str, list] = {}
    for r in all_lines:
        by_receipt.setdefault(r["key"], []).append(r)
    norm = []
    for _, rs in by_receipt.items():
        body_cap = median(
            [
                r["cap_px"]
                for r in rs
                if r["section"] == "ITEMS" and r["cap_px"]
            ]
            or [0]
        )
        body_stroke = median(
            [
                r["stroke_med"]
                for r in rs
                if r["section"] == "ITEMS" and r["stroke_med"]
            ]
            or [0]
        )
        if not body_cap or not body_stroke:
            continue
        for r in rs:
            if r["cap_px"] and r["stroke_med"]:
                norm.append(
                    {
                        **r,
                        "scale": r["cap_px"] / body_cap,
                        "wratio": r["stroke_med"] / body_stroke,
                    }
                )
    priors = {}
    for sec in sorted({r["section"] for r in norm}):
        rs = [r for r in norm if r["section"] == sec]
        wr = median(r["wratio"] for r in rs)
        weight = (
            "bold" if wr >= 1.30 else "semibold" if wr >= 1.15 else "normal"
        )
        priors[sec] = {
            "n_lines": len(rs),
            "n_receipts": len({r["key"] for r in rs}),
            "scale": round(median(r["scale"] for r in rs), 3),
            "weight_ratio": round(wr, 3),
            "weight": weight,
            "underline_rate": round(
                sum(r["underline"] for r in rs) / len(rs), 3
            ),
            "confidence": "ok" if len(rs) >= 3 else "LOW-CONF",
        }
        p = priors[sec]
        print(
            f"{sec:14s} n={p['n_lines']:3d}/{p['n_receipts']:2d}rcpt "
            f"scale={p['scale']:.3f} weight={p['weight_ratio']:.3f}"
            f"({p['weight']}) ul={p['underline_rate']:.2f} "
            f"{p['confidence']}"
        )
    json.dump(
        priors,
        open(os.path.join(args.workdir, "face_priors.json"), "w"),
        indent=1,
    )
    return 0


# --------------------------------------------------------------------------
# borrow: labeled family-borrow atlas variant
# --------------------------------------------------------------------------
def cmd_borrow(args) -> int:
    donor_path = os.path.join(args.bitmatrix, FLEET_ATLASES[args.donor])
    nat = np.load(args.atlas)
    don = np.load(donor_path)
    gates = json.load(open(os.path.join(args.workdir, "gates.json")))
    misrender = {
        f["char"] for f in gates["findings"] if f["kind"] == "MISRENDER"
    }
    nat_cps = {int(k[1:]) for k in nat.files if k.startswith("c")}
    don_cps = {int(k[1:]) for k in don.files if k.startswith("c")}
    payload, label = {}, {}
    for ch in ALL94:
        cp = ord(ch)
        if cp in nat_cps and ch not in misrender:
            src, label[ch] = nat, "native"
        elif cp in don_cps:
            src = don
            label[ch] = f"borrowed({args.donor})" + (
                " replacing misrender" if ch in misrender else ""
            )
        elif cp in nat_cps:
            src, label[ch] = nat, "native(misrender, no borrow available)"
        else:
            label[ch] = "missing->TTF"
            continue
        payload[f"c{cp}"], payload[f"o{cp}"] = src[f"c{cp}"], src[f"o{cp}"]
    out = os.path.join(
        os.path.dirname(args.atlas), "smiths-borrowed.glyphs.npz"
    )
    np.savez_compressed(out, **payload)
    json.dump(
        label,
        open(os.path.join(args.workdir, "borrow_labels.json"), "w"),
        indent=1,
    )
    n_nat = sum(1 for v in label.values() if v.startswith("native"))
    n_bor = sum(1 for v in label.values() if v.startswith("borrowed"))
    n_mis = sum(1 for v in label.values() if v.startswith("missing"))
    print(
        f"wrote {out}: {n_nat} native / {n_bor} borrowed({args.donor}) / "
        f"{n_mis} missing->TTF of {len(ALL94)}"
    )
    return 0


# --------------------------------------------------------------------------
# score: synth ink metrics vs the vetted real distribution
# --------------------------------------------------------------------------
def cmd_score(args) -> int:
    from glyph_review import _ink_metrics
    from PIL import Image

    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    import render_synthetic_receipts as rsr

    example = json.load(open(args.composed))[args.candidate]
    example.setdefault("merchant_name", "Smith's")
    words = rsr._cached_receipt_dict(example)["words"]
    inv = json.load(open(os.path.join(args.workdir, "inventory.json")))
    keys = {
        tuple(k)
        for k in json.load(
            open(os.path.join(args.workdir, "vetted_keys.json"))
        )
    }
    reals = [
        r
        for r in inv["rows"]
        if (r["image_id"], r["receipt_id"]) in keys and r.get("ink")
    ]
    d_all = [r["ink"]["density_med"] for r in reals]
    hw_all = [r["ink"]["h_med"] / r["img_size"][0] for r in reals]
    img = Image.open(args.synth)
    m = _ink_metrics(img, words)
    d_ratio = m["density_med"] / float(np.median(d_all))
    h_ratio = (m["h_med"] / img.width) / float(np.median(hw_all))
    print(
        f"real corpus (n={len(reals)}): density med "
        f"{np.median(d_all):.4f} range {min(d_all):.3f}-{max(d_all):.3f}; "
        f"h/W med {np.median(hw_all):.4f}"
    )
    print(
        f"synth: density {m['density_med']:.4f} (ratio {d_ratio:.3f}) "
        f"h/W {m['h_med'] / img.width:.4f} (ratio {h_ratio:.3f})"
    )
    in_dist = min(d_all) <= m["density_med"] <= max(d_all)
    print(
        "placement: density "
        + ("INSIDE" if in_dist else "OUTSIDE")
        + " the real corpus's range"
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("vet", "gates", "knobs", "faces", "borrow", "score"):
        p = sub.add_parser(name)
        p.add_argument("--workdir", required=True)
        if name in ("gates", "knobs", "borrow"):
            p.add_argument("--atlas", required=True)
        if name in ("gates", "borrow"):
            p.add_argument(
                "--bitmatrix",
                default=os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix"),
            )
        if name == "gates":
            p.add_argument("--top", type=int, default=12)
        if name == "borrow":
            p.add_argument("--donor", default="vons")
        if name == "score":
            p.add_argument("--synth", required=True)
            p.add_argument("--composed", required=True)
            p.add_argument("--candidate", type=int, default=0)
    args = ap.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table
    return {
        "vet": cmd_vet,
        "gates": cmd_gates,
        "knobs": cmd_knobs,
        "faces": cmd_faces,
        "borrow": cmd_borrow,
        "score": cmd_score,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
