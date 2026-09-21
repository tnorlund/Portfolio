#!/usr/bin/env python3
"""new_vendor.py -- take a vendor from "has receipts in dev" to a portfolio pair.

One idempotent command per playbook stage, all driven by
``tools/glyph-studio/fonts/<slug>/vendor.json``::

    census   [--state MI | --name "Whole Foods"]   vendors, receipts, assets
    init     <slug> --merchant "Name" [...]        write vendor.json (+ gold pick)
    corpus   <slug>                                 build + refine the letterform corpus
    font     <slug> [--fix "ABC"]                   glyphstudio.mint (donor fill, specimen, strips)
    pitch    <slug>                                 fleet pitch/cap -> font.json target, weight, condense
    style    <slug>                                 stylescan + styleagg -> stylemap.json draft + lines dump
    profile  <slug>                                 merchant_profiles.json record + env.mjs registration
    fixture  <slug>                                 compile faces to $BITMATRIX_DIR + local truth fixture
    calibrate <slug>                                render vs real, solve ocr_cap_height_ratio, re-render
    export   <slug> [--hero-assets]                 manifest entry + export_pipeline_assets -> portfolio/public
    wire     <slug>                                 regenerate merchants.generated.ts
    all      <slug>                                 corpus .. wire (stops when the font needs --fix)

Reads only from the dev table / buckets. Never publishes, mints or flips
truth: those remain the owner's ``publish_merchant_font.py`` /
``migrate_merchant_truth_v1.py --live`` / ``activate_merchant_truth.py``.
Renders resolve truth ``online-active`` when the vendor already has an
ACTIVE bundle, otherwise from the local fixture this tool builds.

Environment: ``DYNAMODB_TABLE_NAME`` (default dev), ``AWS_REGION``,
``BITMATRIX_DIR`` (default /tmp/bitmatrix), ``RECEIPT_PAPER_STRENGTH``
(default 0.3), ``NEW_VENDOR_STUDIO_ROOT`` (default /tmp/gridfix).
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from typing import Any

from glyphstudio.provenance import exporter_commit

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.abspath(os.path.join(_HERE, ".."))
_ROOT = os.path.abspath(os.path.join(_STUDIO, "..", ".."))
_PKG_PATHS = [
    _HERE,
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_upload"),
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "synthesis_loop"),
]
for _p in _PKG_PATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

FONTS_DIR = os.path.join(_STUDIO, "fonts")
ENV_MJS = os.path.join(_STUDIO, "server", "env.mjs")
PROFILES = os.path.join(_ROOT, "scripts", "merchant_profiles.json")
PIPELINE_PUBLIC = os.path.join(
    _ROOT, "portfolio", "public", "synthetic-receipts", "pipeline"
)
DEV_TABLE = "ReceiptsTable-dc5be22"
TABLE = os.environ.get("DYNAMODB_TABLE_NAME", DEV_TABLE)
REGION = os.environ.get("AWS_REGION", "us-east-1")
BITMATRIX_DIR = os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix")
STUDIO_ROOT = os.environ.get("NEW_VENDOR_STUDIO_ROOT", "/tmp/gridfix")
FINALE_FILES = (
    "real.webp",
    "final.webp",
    "final.labels.json",
    "logo.png",
    "compose_steps.json",
)
H_BAND = (0.95, 1.05)
CAP_RATIO_CLAMP = (0.65, 0.95)  # receipt_renderer clamps ocr_cap_height_ratio


def _assert_dev_table() -> None:
    if TABLE != DEV_TABLE:
        raise SystemExit(
            f"new_vendor only reads the dev table {DEV_TABLE!r}; got {TABLE!r}"
        )


def _client():
    _assert_dev_table()
    from receipt_dynamo import DynamoClient

    return DynamoClient(TABLE)


def _sub_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        _PKG_PATHS + [env.get("PYTHONPATH", "")]
    )
    env.setdefault("DYNAMODB_TABLE_NAME", TABLE)
    env.setdefault("AWS_REGION", REGION)
    env.setdefault("BITMATRIX_DIR", BITMATRIX_DIR)
    env.setdefault("RECEIPT_PAPER_STRENGTH", "0.3")
    env.setdefault("PORTFOLIO_ENV", "dev")
    if extra:
        env.update(extra)
    return env


def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    capture: bool = False,
) -> str:
    print(
        "$ "
        + " ".join(cmd if len(" ".join(cmd)) < 240 else cmd[:4] + ["..."]),
        flush=True,
    )
    res = subprocess.run(
        cmd,
        env=env or _sub_env(),
        cwd=cwd or _ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if res.returncode != 0:
        if capture:
            sys.stderr.write(res.stdout or "")
            sys.stderr.write(res.stderr or "")
        raise SystemExit(
            f"command failed ({res.returncode}): {cmd[1] if len(cmd) > 1 else cmd[0]}"
        )
    return (res.stdout or "") if capture else ""


# ----------------------------------------------------------------- vendor.json


def vendor_path(slug: str) -> str:
    return os.path.join(FONTS_DIR, slug, "vendor.json")


def load_vendor(slug: str) -> dict[str, Any]:
    path = vendor_path(slug)
    if not os.path.exists(path):
        raise SystemExit(
            f"no {path}; run `new_vendor.py init {slug} --merchant ...` first"
        )
    with open(path, encoding="utf-8") as fh:
        v = json.load(fh)
    v.setdefault("slug", slug)
    v.setdefault("portfolio_slug", slug)
    v.setdefault("aliases", [])
    v.setdefault("hero", v["merchant"][0].upper())
    v.setdefault("studio_dir", os.path.join(STUDIO_ROOT, f"{slug}_studio"))
    v.setdefault(
        "graphics", {"footer_codes": False, "inbody_barcode": {"max_count": 0}}
    )
    v.setdefault("label", v["merchant"])
    v.setdefault("bold_callout", "the measured heading weight")
    return v


def save_vendor(v: dict[str, Any]) -> None:
    path = vendor_path(v["slug"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(v, fh, indent=2)
        fh.write("\n")


def _names(v: dict[str, Any]) -> list[str]:
    seen = []
    for n in [v["merchant"], *v.get("aliases", [])]:
        if n.lower() not in {s.lower() for s in seen}:
            seen.append(n)
    return seen


def _gold(v: dict[str, Any]) -> tuple[str, int]:
    g = v.get("gold_receipt") or {}
    if not g.get("image_id"):
        raise SystemExit(
            "vendor.json has no gold_receipt; rerun init with --gold IMAGE#RID"
        )
    return g["image_id"], int(g["receipt_id"])


def _studio(v: dict[str, Any]) -> str:
    os.makedirs(v["studio_dir"], exist_ok=True)
    return v["studio_dir"]


def _refined_npz(v: dict[str, Any]) -> str:
    return os.path.join(v["studio_dir"], f"{v['slug']}.refined.npz")


def _truth_env(v: dict[str, Any], override: str | None) -> dict[str, str]:
    """online-active when an ACTIVE bundle exists, else the local fixture."""
    mode = override
    if mode is None:
        from receipt_dynamo.entities.merchant_catalog_item import (
            slugify_merchant,
        )

        try:
            active = _client().get_active_merchant_truth(
                slugify_merchant(v["merchant"])
            )
        except Exception:  # noqa: BLE001
            active = None
        mode = "online-active" if active else "fixture"
    if mode == "fixture":
        fixture_dir = os.path.join(v["studio_dir"], "fixture")
        if not os.path.isdir(fixture_dir):
            raise SystemExit(
                f"no local fixture at {fixture_dir}; run `fixture {v['slug']}` first"
            )
        return {
            "MERCHANT_TRUTH_MODE": "fixture",
            "MERCHANT_TRUTH_FIXTURE": fixture_dir,
        }
    return {"MERCHANT_TRUTH_MODE": mode}


def _clear_render_cache(v: dict[str, Any]) -> None:
    cache = os.environ.get("RENDER_CACHE_DIR", "/tmp/render_cache")
    token = re.sub(r"[^A-Za-z0-9]+", "_", v["merchant"])
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            if token in name:
                os.remove(os.path.join(cache, name))


# --------------------------------------------------------------------- census


def _receipt_rows(client, places) -> list[dict[str, Any]]:
    rows = []
    for p in places:
        rec: dict[str, Any] = {
            "image_id": p.image_id,
            "receipt_id": p.receipt_id,
            "address": p.formatted_address,
            "merchant": p.merchant_name,
        }
        try:
            rec["type"] = str(
                getattr(client.get_image(p.image_id), "image_type", "")
            ).split(".")[-1]
        except Exception:  # noqa: BLE001
            rec["type"] = "?"
        try:
            d = client.get_receipt_details(p.image_id, p.receipt_id)
            rec["words"] = len(d.words)
            rec["labels"] = len(d.labels)
            rec["dims"] = (d.receipt.width, d.receipt.height)
        except Exception:  # noqa: BLE001
            rec["words"] = rec["labels"] = 0
            rec["dims"] = None
        try:
            secs = client.get_receipt_sections_from_receipt(
                p.image_id, p.receipt_id
            )
            rec["sections"] = len(secs)
            rec["sections_valid"] = sum(
                1
                for s in secs
                if str(getattr(s, "validation_status", "")).upper() == "VALID"
            )
        except Exception:  # noqa: BLE001
            rec["sections"] = rec["sections_valid"] = 0
        rows.append(rec)
    return rows


def _vendor_assets(client, merchant: str) -> dict[str, Any]:
    from receipt_dynamo.entities.merchant_catalog_item import slugify_merchant

    slug = slugify_merchant(merchant)
    with open(PROFILES, encoding="utf-8") as fh:
        profiles = json.load(fh)["profiles"]
    aliases = {k.lower(): k for k in profiles}
    for k, prof in profiles.items():
        for a in prof.get("aliases", []) or []:
            aliases[a.lower()] = k
    try:
        fonts = [f.face for f in client.list_merchant_fonts(merchant)]
    except Exception:  # noqa: BLE001
        fonts = []
    try:
        active = client.get_active_merchant_truth(slug) is not None
    except Exception:  # noqa: BLE001
        active = False
    compact = re.sub(r"[^a-z0-9]", "", merchant.lower())
    font_dirs = [
        d
        for d in os.listdir(FONTS_DIR)
        if os.path.isdir(os.path.join(FONTS_DIR, d))
        and d.replace("_", "") in compact
    ]
    return {
        "merchant_font": fonts,
        "truth_active": active,
        "profile": aliases.get(merchant.lower()),
        "font_dir": font_dirs,
    }


def cmd_census(args) -> int:
    client = _client()
    if args.name:
        names = [n.strip() for n in args.name.split(";") if n.strip()]
        groups = collections.OrderedDict()
        seen_keys: set[tuple[str, int]] = set()
        for n in names:
            rows, _ = client.get_receipt_places_by_merchant(n)
            for p in rows:
                # the GSI is case-insensitive: differently-cased variants
                # return the same receipts, so pool by (image, receipt)
                if (p.image_id, p.receipt_id) in seen_keys:
                    continue
                seen_keys.add((p.image_id, p.receipt_id))
                groups.setdefault(p.merchant_name, []).append(p)
    else:
        places, lek = [], None
        while True:
            page, lek = client.list_receipt_places(
                limit=1000, last_evaluated_key=lek
            )
            places.extend(page)
            if not lek:
                break
        rx = (
            re.compile(rf",\s*{re.escape(args.state)}\b[ ,]", re.I)
            if args.state
            else None
        )
        groups = collections.OrderedDict()
        for p in places:
            if rx and not (
                p.formatted_address and rx.search(p.formatted_address)
            ):
                continue
            groups.setdefault(p.merchant_name, []).append(p)
        groups = collections.OrderedDict(
            sorted(groups.items(), key=lambda kv: -len(kv[1]))
        )
        if args.min_receipts:
            groups = collections.OrderedDict(
                (k, v)
                for k, v in groups.items()
                if len(v) >= args.min_receipts
            )

    print(
        "| merchant_name | receipts | SCAN/PHOTO | words (med) | labels | VALID sections | font / truth / profile / font dir |"
    )
    print("|---|---:|---|---:|---:|---|---|")
    detail = []
    for name, plist in groups.items():
        rows = _receipt_rows(client, plist)
        # chain-wide count (all states) when filtering by state
        total = len(plist)
        if args.state:
            allrows, _ = client.get_receipt_places_by_merchant(name)
            total = len(allrows)
        scans = sum(1 for r in rows if r["type"] == "SCAN")
        assets = _vendor_assets(client, name)
        words = statistics.median([r["words"] for r in rows]) if rows else 0
        print(
            f"| {name} | {len(rows)}{'' if total == len(rows) else f' ({total} chain-wide)'} | {scans}/{len(rows) - scans} | {words:.0f} | "
            f"{sum(r['labels'] for r in rows)} | {sum(r['sections_valid'] for r in rows)}/{sum(r['sections'] for r in rows)} | "
            f"{','.join(assets['merchant_font']) or '-'} / {'ACTIVE' if assets['truth_active'] else '-'} / {assets['profile'] or '-'} / {','.join(assets['font_dir']) or '-'} |"
        )
        detail.append((name, rows))
    if args.receipts:
        for name, rows in detail:
            print(f"\n## {name}")
            for r in sorted(
                rows, key=lambda r: (-(r["type"] == "SCAN"), -r["words"])
            ):
                print(
                    f"  {r['image_id']}#{r['receipt_id']}  {r['type']:5s} words={r['words']:3d} labels={r['labels']:3d} sections={r['sections_valid']}/{r['sections']} {r['dims']}  {r['address']}"
                )
    if args.thumbs:
        import boto3

        s3 = boto3.client("s3", region_name=REGION)
        os.makedirs(args.thumbs, exist_ok=True)
        for name, rows in detail:
            for r in rows:
                rec = client.get_receipt(r["image_id"], r["receipt_id"])
                dest = os.path.join(
                    args.thumbs,
                    f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}_{r['image_id'][:8]}_{r['receipt_id']}.jpg",
                )
                try:
                    s3.download_file(rec.cdn_s3_bucket, rec.cdn_s3_key, dest)
                except Exception as exc:  # noqa: BLE001
                    print(f"  thumb failed {dest}: {exc}")
        print(f"\nthumbnails -> {args.thumbs}")
    return 0


# ----------------------------------------------------------------------- init


def _pick_gold(client, names: list[str]) -> tuple[str, int, dict[str, Any]]:
    best = None
    for n in names:
        rows, _ = client.get_receipt_places_by_merchant(n)
        for r in _receipt_rows(client, rows):
            wide = bool(
                r["dims"] and r["dims"][0] >= 700
            )  # low-res photos make poor gold
            score = (
                r["type"] == "SCAN",
                wide,
                r["sections_valid"],
                r["words"],
            )
            if best is None or score > best[0]:
                best = (score, r)
    if best is None:
        raise SystemExit(f"no receipts for {names}")
    r = best[1]
    return r["image_id"], r["receipt_id"], r


def cmd_init(args) -> int:
    slug = args.slug
    v: dict[str, Any] = {
        "merchant": args.merchant,
        "slug": slug,
        "portfolio_slug": args.portfolio_slug or slug,
        "aliases": args.alias or [],
        "hero": args.hero or args.merchant[0].upper(),
        "label": args.label or args.merchant,
        "bold_callout": args.callout or "the measured heading weight",
        "graphics": {
            "footer_codes": bool(args.footer_codes),
            "inbody_barcode": {"max_count": 0},
        },
        "studio_dir": os.path.join(STUDIO_ROOT, f"{slug}_studio"),
    }
    if args.donor:
        v["donor"] = args.donor
    if args.logo:
        v["logo"] = os.path.relpath(os.path.abspath(args.logo), _ROOT)
    if args.card_logo:
        v["card_logo"] = os.path.relpath(
            os.path.abspath(args.card_logo), _ROOT
        )
    if args.gold:
        image_id, rid = args.gold.split("#")
        v["gold_receipt"] = {"image_id": image_id, "receipt_id": int(rid)}
    else:
        image_id, rid, row = _pick_gold(_client(), _names(v))
        v["gold_receipt"] = {
            "image_id": image_id,
            "receipt_id": int(rid),
            "auto_picked": f"{row['type']} words={row['words']} sections_valid={row['sections_valid']}",
        }
        print(
            f"gold receipt auto-picked: {image_id}#{rid} ({v['gold_receipt']['auto_picked']})"
        )
    if os.path.exists(vendor_path(slug)):
        old = load_vendor(slug)
        old.update(v)
        v = old
    save_vendor(v)
    print(f"wrote {vendor_path(slug)}")
    return 0


# --------------------------------------------------------------------- corpus


def _register_env_mjs(key: str, slug: str, value: str) -> None:
    """Insert ``slug: "value",`` into an env.mjs const block if absent."""
    with open(ENV_MJS, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(rf"export const {key} = \{{(.*?)^\}};", text, re.S | re.M)
    if not m:
        raise SystemExit(f"{key} block not found in env.mjs")
    if re.search(rf"^\s*{re.escape(slug)}:", m.group(1), re.M):
        return
    insert = f"  {slug}: {json.dumps(value)},\n"
    text = text[: m.end(1)] + insert + text[m.end(1) :]
    with open(ENV_MJS, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"env.mjs {key}: registered {slug}")


def cmd_corpus(args) -> int:
    v = load_vendor(args.slug)
    studio = _studio(v)
    names = ";".join(_names(v))
    _run(
        [
            sys.executable,
            os.path.join(_ROOT, "synthesis_loop", "build_merchant_glyphs.py"),
            names,
            studio,
            v["slug"],
        ]
    )
    samples = os.path.join(studio, f"{v['slug']}.samples.npz")
    _run(
        [sys.executable, "-m", "glyphstudio.refine", samples, _refined_npz(v)],
        cwd=_HERE,
    )
    _register_env_mjs("SAMPLES", v["slug"], _refined_npz(v))
    return 0


# ----------------------------------------------------------------------- font


def cmd_font(args) -> int:
    v = load_vendor(args.slug)
    studio = _studio(v)
    font_dir = os.path.join(FONTS_DIR, v["slug"])
    cmd = [
        sys.executable,
        "-m",
        "glyphstudio.mint",
        _refined_npz(v),
        font_dir,
        "--report-dir",
        os.path.join(studio, "mint_report"),
        "--out-npz",
        os.path.join(studio, f"{v['slug']}.glyphs.npz"),
    ]
    if v.get("donor"):
        cmd += ["--donor", v["donor"]]
    for name, chars in (v.get("donor_for") or {}).items():
        cmd += ["--donor-for", f"{name}:{chars}"]
    if args.fix:
        cmd += ["--fix", args.fix]
    _run(cmd, cwd=_HERE)
    return 0


# ---------------------------------------------------------------------- pitch


def measure_pitch(
    client, names: list[str], exclude: set[str]
) -> dict[str, Any]:
    per_receipt = []
    allr = []
    for n in names:
        rows, _ = client.get_receipt_places_by_merchant(n)
        for p in rows:
            key = f"{p.image_id}#{p.receipt_id}"
            if key in exclude:
                continue
            d = client.get_receipt_details(p.image_id, p.receipt_id)
            r = d.receipt
            ratios, caps = [], []
            for w in d.words:
                t = w.text
                if len(t) < 3 or not re.fullmatch(r"[A-Z0-9]+", t):
                    continue
                bb = w.bounding_box
                hpx = bb["height"] * r.height
                if hpx <= 0:
                    continue
                ratios.append((bb["width"] * r.width / len(t)) / hpx)
                caps.append(hpx)
            if ratios:
                per_receipt.append(
                    {
                        "key": key,
                        "n": len(ratios),
                        "pitch_cap": statistics.median(ratios),
                        "cap_px": statistics.median(caps),
                    }
                )
                allr.extend(ratios)
    return {
        "fleet_median": statistics.median(allr) if allr else None,
        "n": len(allr),
        "receipts": per_receipt,
    }


def cmd_pitch(args) -> int:
    from glyphstudio.compile import compile_font
    from glyphstudio.schema import atomic_write_json, load_font

    v = load_vendor(args.slug)
    client = _client()
    m = measure_pitch(client, _names(v), set(v.get("pitch_exclude", [])))
    for r in m["receipts"]:
        print(
            f"  {r['key'][:8]}  n={r['n']:3d}  pitch/cap={r['pitch_cap']:.3f}  cap_px={r['cap_px']:.1f}"
        )
    if m["fleet_median"] is None:
        raise SystemExit("no measurable uppercase/digit words")
    # A slip printed in a visibly different face (Speedway's pump receipts,
    # 0.79 vs 0.49) drags the median; drop receipts >25% off the fleet.
    core = [
        r["pitch_cap"]
        for r in m["receipts"]
        if abs(r["pitch_cap"] - m["fleet_median"]) / m["fleet_median"] <= 0.25
    ]
    target = round(statistics.median(core) if core else m["fleet_median"], 3)
    outliers = [
        r["key"][:8]
        for r in m["receipts"]
        if abs(r["pitch_cap"] - m["fleet_median"]) / m["fleet_median"] > 0.25
    ]
    font_dir = os.path.join(FONTS_DIR, v["slug"])
    font = load_font(font_dir)
    font["metrics"]["pitchRatioTarget"] = target
    font["metrics"]["pitchRatioNote"] = (
        f"median OCR letter pitch / cap across {len(core)} receipts (n={m['n']} words)"
        + (
            f"; outlier slips excluded: {', '.join(outliers)}"
            if outliers
            else ""
        )
    )
    if args.weight is not None:
        font["params"]["weight"] = args.weight
    elif float(font["params"].get("weight", 1.0)) == 1.0:
        font["params"][
            "weight"
        ] = 1.2  # thermal prints are bold (TJ 1.4, CVS 1.35, Vons 1.2)
    atomic_write_json(os.path.join(font_dir, "font.json"), font)
    with tempfile.TemporaryDirectory() as tmp:
        report = compile_font(font_dir, os.path.join(tmp, "probe.npz"))
    condense = round(target / report["advance_ratio"], 3)
    font["preview"]["condense"] = condense
    atomic_write_json(os.path.join(font_dir, "font.json"), font)
    print(
        f"pitchRatioTarget={target} weight={font['params']['weight']} advance_ratio={report['advance_ratio']:.3f} -> condense={condense}"
    )
    return 0


# ---------------------------------------------------------------------- style


def _dump_lines(client, image_id: str, rid: int, out: str) -> None:
    d = client.get_receipt_details(image_id, rid)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# {image_id}#{rid} {d.receipt.width}x{d.receipt.height}\n")
        for line in sorted(d.lines, key=lambda l: -l.bounding_box["y"]):
            fh.write(f"{line.line_id:3d} | {line.text}\n")


def cmd_style(args) -> int:
    from glyphstudio.stylerules import load_stylemap

    v = load_vendor(args.slug)
    studio = _studio(v)
    client = _client()
    scans = os.path.join(studio, "scans")
    os.makedirs(scans, exist_ok=True)
    seen = set()
    keys = []
    for n in _names(v):
        rows, _ = client.get_receipt_places_by_merchant(n)
        for p in rows:
            k = (p.image_id, p.receipt_id)
            if k not in seen:
                seen.add(k)
                keys.append(k)
    keys = keys[: args.max_receipts]
    for image_id, rid in keys:
        out = os.path.join(scans, f"{image_id[:8]}_{rid}.json")
        _run(
            [
                sys.executable,
                "-m",
                "glyphstudio.stylescan",
                image_id,
                str(rid),
                out,
                "--merchant",
                v["slug"],
            ],
            cwd=_HERE,
        )
        _dump_lines(
            client,
            image_id,
            rid,
            os.path.join(studio, f"lines_{image_id[:8]}_{rid}.txt"),
        )
    agg_path = os.path.join(studio, "stylemap-agg.json")
    _run(
        [sys.executable, "-m", "glyphstudio.styleagg", scans, agg_path],
        cwd=_HERE,
    )
    with open(agg_path, encoding="utf-8") as fh:
        agg = json.load(fh)
    stylemap_path = os.path.join(FONTS_DIR, v["slug"], "stylemap.json")
    existing = load_stylemap(v["slug"]) or {}
    previous = existing.get("sections") or {}
    # Rebuild from the aggregate: a section that no rule emits any more
    # disappears; human-set sizeScale/weight/underline survive re-measures.
    sections: dict[str, Any] = {}
    for name, stats in (agg.get("sections") or {}).items():
        stroke = float(((stats.get("stroke_rel") or {}).get("med")) or 1.0)
        cap = float(((stats.get("cap_rel") or {}).get("med")) or 1.0)
        ul = float(stats.get("underline_rate") or 0.0)
        measured = f"n={stats.get('lines')}: cap_rel med {cap:.3f}, stroke_rel med {stroke:.3f}, underline {ul:.2f}."
        if name in previous:
            entry = dict(previous[name])
            human = entry.get("notes", "")
            human = (
                human.split(" || ", 1)[1]
                if " || " in human
                else ("" if human.startswith("n=") else human)
            )
        else:
            entry = {
                "sizeScale": 1.0,
                "weight": "bold" if stroke >= 1.12 else "normal",
                "underline": False,
                "underlineRate": round(ul, 2),
            }
            human = "AUTO-DRAFT: confirm sizeScale/weight against the strips."
        entry["notes"] = f"{measured} || {human}".rstrip(" |")
        sections[name] = entry
    stylemap = {
        "version": 1,
        "source": {
            "merchant": v["slug"],
            "receipts": agg.get("receipts_used"),
            "scan_dir": scans,
            "lines_measured": sum(
                int((s.get("lines") or 0))
                for s in (agg.get("sections") or {}).values()
            ),
            "date": __import__("datetime").date.today().isoformat(),
            "notes": existing.get("source", {}).get(
                "notes",
                "Drafted by new_vendor.py style; rules are hand-authored from the lines_*.txt dumps in the studio dir.",
            ),
        },
        "rules": existing.get("rules") or [],
        "sections": sections,
    }
    with open(stylemap_path, "w", encoding="utf-8") as fh:
        json.dump(stylemap, fh, indent=2)
        fh.write("\n")
    print(
        f"stylemap -> {stylemap_path} ({len(sections)} sections; rules: {len(stylemap['rules'])})"
    )
    if not stylemap["rules"]:
        print(
            f"  no `rules` yet: classes fell back to the generic Sprouts rules. Author `rules` from {studio}/lines_*.txt, then rerun `style` to re-measure per section."
        )
    return 0


# -------------------------------------------------------------------- profile


def cmd_profile(args) -> int:
    from glyphstudio.schema import load_font

    v = load_vendor(args.slug)
    font = load_font(os.path.join(FONTS_DIR, v["slug"]))
    with open(PROFILES, encoding="utf-8") as fh:
        doc = json.load(fh, object_pairs_hook=collections.OrderedDict)
    profiles = doc["profiles"]
    if (
        v["merchant"] in profiles
        and not v.get("owns_profile")
        and not getattr(args, "force", False)
    ):
        raise SystemExit(
            f"{v['merchant']!r} already has a profile in merchant_profiles.json "
            "(a vendor minted before new_vendor.py, or an ACTIVE truth bundle). "
            "Pass --force to overwrite its typography from font.json, or skip "
            "this stage."
        )
    rec = profiles.get(v["merchant"], collections.OrderedDict())
    v["owns_profile"] = True
    save_vendor(v)
    rec["_comment"] = (
        rec.get("_comment")
        or f"Minted by new_vendor.py from {v['merchant']}'s dev receipts (font {v['slug']}). Edit typography knobs here; `new_vendor.py calibrate` solves ocr_cap_height_ratio."
    )
    rec["aliases"] = list(v.get("aliases", []))
    typ = rec.get("typography", collections.OrderedDict())
    typ.update(
        {
            "font": "PTMONO",
            "bitmap_font": {
                "regular": f"{v['slug']}.glyphs.npz",
                "heavy": f"{v['slug']}-heavy.glyphs.npz",
            },
            "condense": float(font["preview"]["condense"]),
            "mixed_layout": True,
            "bitmap_cap_ratio": typ.get("bitmap_cap_ratio", 0.66),
            "bitmap_thin": typ.get("bitmap_thin", 0.0),
            "ocr_font_sizing": True,
            "ocr_cap_height_ratio": typ.get("ocr_cap_height_ratio", 0.72),
            "stylemap": f"{v['slug']}.stylemap.json",
            "pitch_ratio": float(
                font["metrics"].get("pitchRatioTarget") or 0.5
            ),
        }
    )
    rec["typography"] = typ
    if "section_scale" in v:
        # e.g. {"HEADER": 1.0} = no default 0.8 HEADER shrink. Use a
        # non-empty map: the v1 mint collapses an explicit {} and the
        # profile-parity test then fails the round-trip.
        rec["section_scale"] = v["section_scale"]
    if v.get("logo"):
        rec["logo"] = f"{v['slug']}_logo.png"
        rec.setdefault(
            "logo_anchor",
            {
                "phrases": v.get("logo_phrases") or [v["merchant"].upper()],
                "extend_left": False,
                "center": True,
            },
        )
    rec["graphics"] = v.get("graphics")
    profiles[v["merchant"]] = rec
    with open(PROFILES, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=True)
        fh.write("\n")
    _register_env_mjs("FONT_MERCHANTS", v["slug"], v["merchant"])
    print(
        f"profile {v['merchant']!r} written to scripts/merchant_profiles.json"
    )
    return 0


# -------------------------------------------------------------------- fixture


def _stage_faces(v: dict[str, Any]) -> None:
    from glyphstudio.compile import compile_font
    from publish_merchant_font import _heavy_variant_dir

    font_dir = os.path.join(FONTS_DIR, v["slug"])
    os.makedirs(BITMATRIX_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for face, src in (("regular", font_dir), ("heavy", None)):
            d = src or _heavy_variant_dir(font_dir, tmp)
            name = (
                f"{v['slug']}.glyphs.npz"
                if face == "regular"
                else f"{v['slug']}-heavy.glyphs.npz"
            )
            dest = os.path.join(BITMATRIX_DIR, name)
            if os.path.islink(dest):
                os.unlink(dest)
            report = compile_font(d, dest)
            print(
                f"  {face}: {name} coverage {report['coverage']}/94 cap {report['cap_h']:.0f} advance {report['advance_ratio']:.3f}"
            )
    if v.get("logo"):
        shutil.copyfile(
            os.path.join(_ROOT, v["logo"]),
            os.path.join(BITMATRIX_DIR, f"{v['slug']}_logo.png"),
        )


def cmd_fixture(args) -> int:
    from receipt_dynamo.entities.merchant_catalog_item import slugify_merchant

    v = load_vendor(args.slug)
    studio = _studio(v)
    _stage_faces(v)
    fixture_dir = os.path.join(studio, "fixture")
    shutil.rmtree(fixture_dir, ignore_errors=True)
    _run(
        [
            sys.executable,
            os.path.join(_ROOT, "scripts", "migrate_merchant_truth_v1.py"),
            "--profiles",
            PROFILES,
            "--stylemap-root",
            FONTS_DIR,
            "--merchants",
            slugify_merchant(v["merchant"]),
            "--output-dir",
            os.path.join(studio, "truth_v1"),
            "--fixture-out",
            fixture_dir,
            "--asset-dir",
            BITMATRIX_DIR,
        ]
    )
    _clear_render_cache(v)
    return 0


# ------------------------------------------------------------------ calibrate

_METRICS_RE = re.compile(
    r"h_ratio=([\d.]+).*?wpc_med=([\d.]+).*?synth_wpc_med=([\d.]+).*?density_ratio=([\d.]+)"
)


def _render_review(
    v: dict[str, Any], tag: str, truth: str | None
) -> dict[str, float]:
    image_id, rid = _gold(v)
    out = os.path.join(_studio(v), f"review_{tag}.png")
    _clear_render_cache(v)
    text = _run(
        [
            sys.executable,
            os.path.join(_ROOT, "synthesis_loop", "glyph_review.py"),
            "receipt",
            v["merchant"],
            image_id,
            str(rid),
            out,
        ],
        env=_sub_env(_truth_env(v, truth)),
        capture=True,
    )
    line = next((l for l in text.splitlines() if l.startswith("metrics ")), "")
    m = re.search(r"h_ratio=([\d.]+)", line)
    w1 = re.search(r"real_wpc_med=([\d.]+)", line)
    w2 = re.search(r"synth_wpc_med=([\d.]+)", line)
    dr = re.search(r"density_ratio=([\d.]+)", line)
    if not (m and w1 and w2 and dr):
        sys.stdout.write(text)
        raise SystemExit("could not parse the glyph_review metrics line")
    print("  " + line)
    # Optional read-back of the renderer's advance-clamp geometry: the OCR
    # word-start pitch (its measured advance) and the rendered cap height
    # (its cap_px), both in synth pixels. Missing/zero -> None.
    pitch_px = re.search(r"ocr_pitch_med=([\d.]+)", line)
    cap_px = re.search(r"synth_h_med=([\d.]+)", line)
    return {
        "h_ratio": float(m.group(1)),
        "wpc_ratio": float(w2.group(1)) / float(w1.group(1)),
        "density_ratio": float(dr.group(1)),
        "ocr_pitch_px": float(pitch_px.group(1)) if pitch_px else None,
        "synth_cap_px": float(cap_px.group(1)) if cap_px else None,
        "png": out,
        "scorecard": out.replace(".png", ".scorecard.md"),
    }


def _set_profile_knob(merchant: str, key: str, value: Any) -> None:
    with open(PROFILES, encoding="utf-8") as fh:
        doc = json.load(fh, object_pairs_hook=collections.OrderedDict)
    doc["profiles"][merchant]["typography"][key] = value
    with open(PROFILES, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=True)
        fh.write("\n")


def _set_vendor_export_pin(slug: str, key: str, value: Any) -> None:
    """Write an export pin into fonts/<slug>/vendor.json without load_vendor defaults."""
    path = vendor_path(slug)
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc[key] = value
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _export_ocr_cap_pin(v: dict[str, Any], typ: dict[str, Any]) -> float:
    """The pin export overlays: vendor.json, else the reviewed profile."""
    raw = v.get("ocr_cap_height_ratio")
    if raw is not None:
        return float(raw)
    return float(typ.get("ocr_cap_height_ratio", 0.72))


def _export_pitch_pin(v: dict[str, Any], typ: dict[str, Any]) -> float:
    """The pitch_ratio export renders: vendor.json pin, else the profile.

    A vendor.json ``pitch_ratio`` is an opt-in pin (``gold_render_pins``)
    that wins over both the profile and font.json ``pitchRatioTarget``, so
    calibrate must start from -- and write back to -- the same value.
    """
    raw = v.get("pitch_ratio")
    if raw is not None:
        return float(raw)
    return float(typ.get("pitch_ratio", 0.55))


def _record_calibrated_knob(v: dict[str, Any], key: str, value: float) -> None:
    """Persist a solved knob where BOTH the review and the export read it.

    The profile is what the review render resolves through the truth
    registry; ``fonts/<slug>/vendor.json`` is the export pin that
    ``apply_gold_pins`` overlays. Writing one without the other lets a
    passing calibration describe a receipt the export does not render.
    """
    _set_profile_knob(v["merchant"], key, value)
    _set_vendor_export_pin(v["slug"], key, value)
    v[key] = value


def _in_band(x: float) -> bool:
    return H_BAND[0] <= x <= H_BAND[1]


# receipt_renderer: the grid advance is clamped to pitch_ratio x cap x this.
PITCH_CLAMP = (0.85, 1.15)
PITCH_EDGE_MARGIN = 0.02
# Bounded expansion when a re-render is unmoved: at most this many steps,
# never outside these pitch_ratio bounds; only then is it saturation.
PITCH_EXPANSION_STEPS = 4
PITCH_BOUNDS = (0.3, 1.2)
PITCH_MIN_STEP = 0.02


def pitch_past_clamp_edge(
    pitch_ratio: float, metrics: dict[str, Any]
) -> tuple[float | None, str]:
    """A pitch_ratio whose clamp EDGE lands the advance on target.

    Under ocr_font_sizing the advance is the measured OCR word-start pitch
    clamped to ``pitch_ratio * cap_px * [0.85, 1.15]``. While the measured
    advance lies INSIDE that interval the clamp is inert and a small
    pitch_ratio move changes nothing -- a deadband, not saturation. For wpc
    low the floor must rise above the measured advance,
    ``pitch > measured / (0.85 * cap)``; for wpc high the ceiling must drop
    below it, ``pitch < measured / (1.15 * cap)``; the solve lands wpc on
    1.0 and always clears the edge by ``PITCH_EDGE_MARGIN``. The geometry
    comes from the review metrics (ocr_pitch_med, synth_h_med), which only
    approximate the renderer's own numbers; ``None`` when it is missing.
    """
    wpc = float(metrics["wpc_ratio"])
    measured = metrics.get("ocr_pitch_px") or 0.0
    cap = metrics.get("synth_cap_px") or 0.0
    if not (measured > 0 and cap > 0):
        return None, "no clamp geometry in the review metrics"
    lo, hi = PITCH_CLAMP
    if wpc < H_BAND[0]:
        edge = measured / (lo * cap)
        target = measured / (wpc * lo * cap)
        new = max(target, edge * (1 + PITCH_EDGE_MARGIN))
    else:
        edge = measured / (hi * cap)
        target = measured / (wpc * hi * cap)
        new = min(target, edge * (1 - PITCH_EDGE_MARGIN))
    return round(new, 3), (
        f"measured advance {measured:.2f}px sits inside the clamp "
        f"[{pitch_ratio * lo * cap:.2f}, {pitch_ratio * hi * cap:.2f}]px "
        f"(deadband); clamp edge at pitch_ratio {edge:.3f}"
    )


def next_pitch_step(
    pitch_ratio: float,
    last_pitch: float | None,
    metrics: dict[str, Any],
    steps_taken: int,
) -> tuple[float | None, str]:
    """The next pitch_ratio to try after an unmoved re-render, or None.

    Step one is the clamp-edge solve when the review carries usable
    geometry and it points the way wpc says (a review pitch that disagrees
    with the renderer's, e.g. a sparse receipt where the renderer fell back
    to box widths, can point the wrong way; it is then ignored). Every
    further step doubles the last pitch delta in the direction wpc needs.
    Saturation is declared -- ``None`` -- only once ``PITCH_EXPANSION_STEPS``
    are spent or the next step would leave ``PITCH_BOUNDS``.
    """
    if steps_taken >= PITCH_EXPANSION_STEPS:
        return None, (
            f"{PITCH_EXPANSION_STEPS} expansion steps did not move the render"
        )
    direction = 1.0 if float(metrics["wpc_ratio"]) < H_BAND[0] else -1.0
    why = ""
    new = None
    if steps_taken == 0:
        solved, why = pitch_past_clamp_edge(pitch_ratio, metrics)
        if solved is not None and (solved - pitch_ratio) * direction > 0:
            new = solved
        elif solved is not None:
            why = (
                f"review clamp geometry points the wrong way "
                f"(solved {solved} vs {pitch_ratio}); it disagrees with "
                "the renderer, ignoring it"
            )
    if new is None:
        delta = 0.0 if last_pitch is None else abs(pitch_ratio - last_pitch)
        delta = max(2 * delta, PITCH_MIN_STEP)
        new = round(pitch_ratio + direction * delta, 3)
        why = (why + "; " if why else "") + (
            f"expansion step {steps_taken + 1}/{PITCH_EXPANSION_STEPS}: "
            f"{'raising' if direction > 0 else 'lowering'} pitch_ratio by "
            f"{delta:.3f}"
        )
    if not PITCH_BOUNDS[0] <= new <= PITCH_BOUNDS[1]:
        return None, (
            f"next step {new} would leave pitch_ratio bounds {PITCH_BOUNDS}"
        )
    return new, why


def cmd_calibrate(args) -> int:
    """Solve ocr_cap_height_ratio / pitch_ratio against the gold receipt.

    ``vendor.json`` is the export pin. Every knob the loop solves is written
    to that pin AND the merchant profile (``_record_calibrated_knob``) so
    ``cmd_export`` without ``--calibrate-from-corpus`` renders the same
    values the review just scored.
    """
    v = load_vendor(args.slug)
    with open(PROFILES, encoding="utf-8") as fh:
        typ = json.load(fh)["profiles"][v["merchant"]]["typography"]
    ratio = _export_ocr_cap_pin(v, typ)
    pitch_ratio = _export_pitch_pin(v, typ)
    fixture_mode = (
        args.truth or _truth_env(v, args.truth).get("MERCHANT_TRUTH_MODE")
    ) == "fixture"
    metrics = _render_review(v, "cal0", args.truth)
    last_wpc = None
    last_pitch: float | None = None
    edge_steps = 0
    for i in range(1, args.iterations + 1):
        if _in_band(metrics["h_ratio"]) and _in_band(metrics["wpc_ratio"]):
            break
        changed = False
        wpc_stuck = (
            last_wpc is not None
            and abs(metrics["wpc_ratio"] - last_wpc) < 0.005
        )
        if not _in_band(metrics["h_ratio"]):
            # cap_px = median(OCR box heights) * clamp(ratio, 0.65, 0.95):
            # h_ratio is linear in the ratio, so one solve lands it (the
            # renderer clamps).
            solved = max(
                CAP_RATIO_CLAMP[0],
                min(CAP_RATIO_CLAMP[1], ratio / metrics["h_ratio"]),
            )
            if abs(solved - ratio) < 1e-3:
                print(
                    f"  ocr_cap_height_ratio is pinned at the renderer clamp {solved}; h_ratio {metrics['h_ratio']:.3f} needs a weight/glyph change, not this knob"
                )
            else:
                print(
                    f"  ocr_cap_height_ratio {ratio} -> {solved:.3f} (h_ratio {metrics['h_ratio']:.3f})"
                )
                ratio = round(solved, 3)
                _record_calibrated_knob(v, "ocr_cap_height_ratio", ratio)
                changed = True
        if not _in_band(metrics["wpc_ratio"]) and wpc_stuck:
            # An unmoved re-render is only saturation once the clamp edge
            # has been crossed; inside the clamp interval the knob is in a
            # deadband (Codex P2 on #1711). Expand, bounded, then judge.
            stepped, why = next_pitch_step(
                pitch_ratio, last_pitch, metrics, edge_steps
            )
            edge_steps += 1
            if stepped is not None and abs(stepped - pitch_ratio) > 1e-9:
                print(
                    f"  wpc_ratio unmoved at {metrics['wpc_ratio']:.3f}: {why}; pitch_ratio {pitch_ratio} -> {stepped}"
                )
                last_pitch = pitch_ratio
                pitch_ratio = stepped
                _record_calibrated_knob(v, "pitch_ratio", pitch_ratio)
                changed = True
            else:
                print(
                    f"  wpc_ratio did not respond to pitch_ratio (stuck at {metrics['wpc_ratio']:.3f}; {why}); the rendered glyph ink per char is bounded by the cell, not this knob. Leaving pitch_ratio at {pitch_ratio}."
                )
        elif not _in_band(metrics["wpc_ratio"]):
            # Under ocr_font_sizing the grid pitch comes from the OCR word
            # starts, clamped to profile pitch_ratio x cap x [0.85, 1.15];
            # font.json condense is inert there. Moving pitch_ratio moves the
            # clamp floor/ceiling, which is what binds on skewed photos.
            solved_p = round(pitch_ratio / metrics["wpc_ratio"], 3)
            print(
                f"  pitch_ratio {pitch_ratio} -> {solved_p} (wpc_ratio {metrics['wpc_ratio']:.3f})"
            )
            last_pitch = pitch_ratio
            pitch_ratio = solved_p
            _record_calibrated_knob(v, "pitch_ratio", pitch_ratio)
            changed = True
        if not changed:
            break
        if fixture_mode:
            cmd_fixture(argparse.Namespace(slug=v["slug"]))
        last_wpc = metrics["wpc_ratio"]
        metrics = _render_review(v, f"cal{i}", args.truth)
    ok = (
        H_BAND[0] <= metrics["h_ratio"] <= H_BAND[1]
        and H_BAND[0] <= metrics["wpc_ratio"] <= H_BAND[1]
        and metrics["density_ratio"] >= 0.85
    )
    print(
        f"gate {'PASS' if ok else 'FAIL'}: h_ratio={metrics['h_ratio']:.3f} wpc_ratio={metrics['wpc_ratio']:.3f} density_ratio={metrics['density_ratio']:.3f}  -> {metrics['png']}"
    )
    if metrics["density_ratio"] < 0.85:
        print(
            f"  density low: raise font.json params.weight (~x{1 / metrics['density_ratio']:.2f}), rerun `pitch` (recomputes condense) then `fixture` + `calibrate`"
        )
    elif metrics["density_ratio"] > 1.2:
        print(
            f"  density high: lower font.json params.weight (~x{1 / metrics['density_ratio']:.2f}) or raise profile bitmap_thin; rerun `pitch`, `fixture`, `calibrate`"
        )
    if not _in_band(metrics["wpc_ratio"]):
        print(
            f"  wpc still off after {args.iterations} iterations (pitch_ratio now {pitch_ratio}); check the gold receipt's OCR word boxes for skew"
        )
    print(f"  scorecard: {metrics['scorecard']}")
    return 0 if ok else 1


# --------------------------------------------------------------------- export


def cmd_export(args) -> int:
    from glyphstudio import portfolio_wiring as pw

    v = load_vendor(args.slug)
    # set_entry writes the tracked manifest, so a check inside the exporter
    # would see a dirty tree on a clean export. Validate once, first.
    commit = exporter_commit(_ROOT)
    studio = _studio(v)
    image_id, rid = _gold(v)
    pslug = v["portfolio_slug"]
    pw.set_entry(
        pslug,
        merchant=v["merchant"],
        font=v["slug"],
        hero=v["hero"],
        image_id=image_id,
        receipt_id=rid,
        label=v["label"],
        bold_callout=v["bold_callout"],
    )
    # The exporter reads this manifest next. A source snapshot whose
    # manifest_receipt is not the receipt just written is rejected there
    # instead of silently rendering the old pin.
    out_root = os.path.join(studio, "pipeline_export")
    cmd = [
        sys.executable,
        os.path.join(_HERE, "export_pipeline_assets.py"),
        pslug,
        "--out-dir",
        out_root,
        "--cache-dir",
        os.path.join(studio, "merchant_gold_cache"),
    ]
    if not args.hero_assets:
        cmd.append("--finale-only")
    elif os.path.exists(_refined_npz(v)):
        cmd += ["--corpus", _refined_npz(v)]
    # `logo` is the print's own wordmark (renderer logo band + card);
    # `card_logo` is a card-only mark for vendors whose print carries no
    # logo graphic (Speedway, Burritt's) so the finale card still has one.
    card_logo = v.get("card_logo") or v.get("logo")
    if card_logo:
        cmd += ["--logo", os.path.join(_ROOT, card_logo)]
    if commit is not None:
        cmd += ["--exporter-commit", commit]
    _clear_render_cache(v)
    text = _run(cmd, env=_sub_env(_truth_env(v, args.truth)), capture=True)
    sys.stdout.write(text)
    m = re.search(
        rf"^\s*{re.escape(pslug)}: \{{ w: (\d+), h: (\d+) \}}", text, re.M
    )
    if not m:
        raise SystemExit("exporter did not report RECEIPT_DIMS")
    dims = {"w": int(m.group(1)), "h": int(m.group(2))}
    pw.set_dims(pslug, dims)
    src = os.path.join(out_root, pslug)
    dest = os.path.join(PIPELINE_PUBLIC, pslug)
    if args.hero_assets:
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
    else:
        os.makedirs(dest, exist_ok=True)
        for f in FINALE_FILES:
            if os.path.exists(os.path.join(src, f)):
                shutil.copyfile(os.path.join(src, f), os.path.join(dest, f))
    missing = [
        f for f in FINALE_FILES if not os.path.exists(os.path.join(dest, f))
    ]
    print(
        f"portfolio assets -> {dest} dims={dims}"
        + (f"  MISSING: {missing}" if missing else "")
    )
    return 1 if missing else 0


def cmd_wire(args) -> int:
    from glyphstudio import portfolio_wiring as pw

    merchants = pw.load_manifest()
    problems = pw.validate(merchants)
    if problems:
        for p in problems:
            print("manifest:", p)
        return 2
    pw.write_ts(merchants)
    print(f"wrote {pw.GENERATED_TS} ({len(merchants)} merchants)")
    print(
        "next: cd portfolio && npm run lint && npm run type-check && npm test"
    )
    return 0


def cmd_all(args) -> int:
    for stage in (
        "corpus",
        "font",
        "pitch",
        "style",
        "profile",
        "fixture",
        "calibrate",
        "export",
        "wire",
    ):
        print(f"\n=== {stage} {args.slug} ===")
        ns = argparse.Namespace(
            **{
                **vars(args),
                "fix": getattr(args, "fix", ""),
                "weight": None,
                "max_receipts": 12,
                "truth": getattr(args, "truth", None),
                "iterations": 2,
                "hero_assets": getattr(args, "hero_assets", False),
            }
        )
        rc = COMMANDS[stage](ns)
        if rc and stage in ("font", "calibrate"):
            print(
                f"stopping after {stage} (rc={rc}); fix and rerun from there"
            )
            return rc
    return 0


COMMANDS = {
    "census": cmd_census,
    "init": cmd_init,
    "corpus": cmd_corpus,
    "font": cmd_font,
    "pitch": cmd_pitch,
    "style": cmd_style,
    "profile": cmd_profile,
    "fixture": cmd_fixture,
    "calibrate": cmd_calibrate,
    "export": cmd_export,
    "wire": cmd_wire,
    "all": cmd_all,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("census")
    c.add_argument(
        "--state",
        help="two-letter state in the ReceiptPlace formatted address",
    )
    c.add_argument("--name", help="merchant_name(s), ';'-separated")
    c.add_argument("--min-receipts", type=int, default=0)
    c.add_argument(
        "--receipts", action="store_true", help="list every receipt"
    )
    c.add_argument("--thumbs", help="download CDN images into this dir")

    i = sub.add_parser("init")
    i.add_argument("slug")
    i.add_argument(
        "--merchant",
        required=True,
        help="ReceiptPlace.merchant_name (display name)",
    )
    i.add_argument(
        "--alias", action="append", help="name variant to pool (repeatable)"
    )
    i.add_argument(
        "--gold",
        help="IMAGE_ID#RECEIPT_ID (default: auto-pick SCAN with most VALID sections/words)",
    )
    i.add_argument("--hero")
    i.add_argument(
        "--donor", help="sibling font dir to adopt thin/missing glyphs from"
    )
    i.add_argument("--label", help="finale card label")
    i.add_argument("--callout", help="act-4 bold weight callout")
    i.add_argument(
        "--logo",
        help="black-on-white L-mode wordmark PNG (repo path) that the PRINT "
        "carries -> renderer logo band + finale card",
    )
    i.add_argument(
        "--card-logo",
        help="card-only wordmark PNG for prints with no logo graphic "
        "(no renderer band)",
    )
    i.add_argument(
        "--footer-codes",
        action="store_true",
        help="the print carries a footer QR/barcode",
    )
    i.add_argument("--portfolio-slug")

    for name in ("corpus", "pitch", "style", "profile", "fixture", "wire"):
        p = sub.add_parser(name)
        p.add_argument("slug")
        if name == "profile":
            p.add_argument(
                "--force",
                action="store_true",
                help="overwrite a pre-existing profile's typography",
            )
        if name == "pitch":
            p.add_argument("--weight", type=float)
        if name == "style":
            p.add_argument("--max-receipts", type=int, default=12)
    f = sub.add_parser("font")
    f.add_argument("slug")
    f.add_argument("--fix", default="", help="chars the strip triage rejected")
    cal = sub.add_parser("calibrate")
    cal.add_argument("slug")
    cal.add_argument("--iterations", type=int, default=2)
    cal.add_argument("--truth", choices=["fixture", "online-active"])
    e = sub.add_parser("export")
    e.add_argument("slug")
    e.add_argument(
        "--hero-assets",
        action="store_true",
        help="also char_prints/font_grid/... (needs the refined corpus)",
    )
    e.add_argument("--truth", choices=["fixture", "online-active"])
    a = sub.add_parser("all")
    a.add_argument("slug")
    a.add_argument("--fix", default="")
    a.add_argument("--truth", choices=["fixture", "online-active"])
    a.add_argument("--hero-assets", action="store_true")

    args = ap.parse_args(argv)
    return COMMANDS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
