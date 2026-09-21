#!/usr/bin/env python3
"""export_pipeline_assets.py -- rebuild a merchant's SynthesisPipeline assets.

Writes the complete ``portfolio/public/synthetic-receipts/pipeline/<slug>/``
tree for one merchant from the committed tooling, so the figure on
``/receipt`` can be regenerated instead of hand-assembled:

finale pair (every merchant)
  final.webp / final.labels.json  production render of the source receipt
                                  (closed gold recipe: font.json pitch +
                                  recorded cap / bitmap_thin, render-true boxes)
  real.webp                       the real scan at the same 760xH canvas
  logo.png                        the vault logo as an RGBA alpha mask
  compose_steps.json              reveal groups (y-bands of the receipt)
  pipeline_merchants.json         per-card provenance (source_snapshot_sha256,
                                  font/logo hashes, final_webp_sha256,
                                  exporter_commit, image_type)

hero acts (merchants with a letterform corpus + glyph-studio font)
  char_prints/{0..29}.png         real prints of the hero character
  char_cloud.png                  soft consensus of the whole corpus
  char_skeleton.json              the glyph source JSON, verbatim
  dot_params.json                 dot size / weights / cloud geometry
  font_grid/{33..126}.png         the renderer's glyph masks at cap 40
  font_metrics.json               per-glyph width/height/baseline offset
  style_annotated.json            notable stylemap sections + crops
  style_crops/<section>.png       the matching lines on the real scan
  real_thumbs/{0,1,2}.webp        act-1 fan of real scans

Inputs and where they come from:

* ``tools/glyph-studio/fixtures/pipeline_merchants.json`` names the source
  receipt, canonical merchant, font dir and hero character per slug.
* Receipt words/labels/barcodes, the ReceiptPlace check, receipt dims and
  the CDN scan come from the DEV table ``ReceiptsTable-dc5be22`` and its
  buckets (read-only). The receipt payload is cached under ``--cache-dir``
  after the first pull (same cache as ``render_merchant_gold.py``).
* Fonts, logo and stylemap resolve through the ACTIVE merchant-truth bundle
  exactly as production renders do (``scripts/render_synthetic_receipts``).
* The letterform corpus is ``merchant_fonts/<font>/corpus.npz`` in the font
  vault (or ``--corpus`` for a local ``*.samples.npz``).
* Glyph skeletons and the stylemap are read from
  ``tools/glyph-studio/fonts/<font>/``; the font grid is rendered by the
  renderer's own ``BitmapFont`` from a fresh compile of that dir.

Env (same as glyph_review receipt mode): AWS credentials with read access
to the dev table + buckets, ``DYNAMODB_TABLE_NAME`` (default dev),
``AWS_REGION``, ``BITMATRIX_DIR`` (font cache), ``RECEIPT_PAPER_STRENGTH``
(0.3 matches the shipped assets), ``MERCHANT_TRUTH_MODE`` (default
online-active).

Usage:
    export_pipeline_assets.py sprouts --out-dir /tmp/pipeline
    export_pipeline_assets.py vons --out-dir /tmp/pipeline --finale-only
    export_pipeline_assets.py --all --out-dir /tmp/pipeline

The tool never writes into ``portfolio/public``; copy the tree over after
reviewing it and update ``RECEIPT_DIMS`` in ``pipelineData.ts`` with the
dims the tool prints.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from io import BytesIO
from typing import TYPE_CHECKING, Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.abspath(os.path.join(_HERE, ".."))
_ROOT = os.path.abspath(os.path.join(_STUDIO, "..", ".."))
for _p in (
    _HERE,
    os.path.join(_ROOT, "receipt_agent"),
    os.path.join(_ROOT, "receipt_dynamo"),
    os.path.join(_ROOT, "receipt_upload"),
    os.path.join(_ROOT, "scripts"),
    os.path.join(_ROOT, "synthesis_loop"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boto3  # noqa: E402
import numpy as np  # noqa: E402
import render_synthetic_receipts as rsr  # noqa: E402
from glyphstudio import pipeline_assets as pa  # noqa: E402
from glyphstudio.compile import compile_font  # noqa: E402
from glyphstudio.provenance import (  # noqa: E402
    card_provenance,
    exporter_commit,
    write_manifest_provenance,
)
from glyphstudio.schema import glyph_filename, load_font  # noqa: E402
from glyphstudio.stylescan import _classify, line_has_price  # noqa: E402
from glyphstudio.vendor_package import resolve_gold_inputs  # noqa: E402
from PIL import Image  # noqa: E402
from render_merchant_gold import (  # noqa: E402
    _cached_payload,
    closed_font_profile,
)

from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (  # noqa: E402
    BitmapFont,
)
from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

DEFAULT_MANIFEST = os.path.join(_STUDIO, "fixtures", "pipeline_merchants.json")
FONTS_DIR = os.path.join(_STUDIO, "fonts")
DEFAULT_TABLE = "ReceiptsTable-dc5be22"
CORPUS_BUCKET_ENV = "MERCHANT_FONT_BUCKET"
CORPUS_BUCKET_DEFAULT = "raw-image-bucket-c779c32"


def load_manifest(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["merchants"]


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
        fh.write("\n")


def _save_webp(image: Image.Image, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image.convert("RGB").save(
        path, format="WEBP", quality=pa.WEBP_QUALITY, method=6
    )


def _compact(name: str) -> str:
    return "".join(ch for ch in name.upper() if ch.isalnum())


def _same_merchant(place_name: str, merchant: str) -> bool:
    """Does a ReceiptPlace name belong to the manifest merchant?

    Same truth-bundle profile (aliases included), or the Places display
    name is the brand plus a store descriptor ("Wild Fork Meat & Seafood
    Market - Thousand Oaks" for "Wild Fork").
    """
    if _compact(place_name).startswith(_compact(merchant)):
        return True
    try:
        return rsr.get_merchant_profile_key(place_name)[0] == (
            rsr.get_merchant_profile_key(merchant)[0]
        )
    except Exception:  # noqa: BLE001 - unknown alias -> not the same
        return False


def _load_s3_image(s3: S3Client, bucket: str | None, key: str | None):
    if not bucket or not key:
        return None
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception:  # noqa: BLE001 - fall through to the next copy
        return None
    return Image.open(BytesIO(body)).convert("RGB")


def load_real_scan(s3: S3Client, receipt: Any) -> Image.Image:
    """The receipt crop: CDN derivative first, raw upload as fallback."""
    for bucket, key in (
        (receipt.cdn_s3_bucket, receipt.cdn_s3_key),
        (receipt.raw_s3_bucket, receipt.raw_s3_key),
    ):
        image = _load_s3_image(s3, bucket, key)
        if image is not None:
            return image
    raise RuntimeError(
        f"no scan for {receipt.image_id}#{receipt.receipt_id} in S3"
    )


class Exporter:
    def __init__(
        self,
        *,
        table: str,
        region: str,
        cache_dir: str,
        corpus_bucket: str,
        calibrate_from_corpus: bool = False,
    ) -> None:
        self.table = table
        self.region = region
        self.cache_dir = cache_dir
        self.corpus_bucket = corpus_bucket
        self.calibrate_from_corpus = calibrate_from_corpus
        self.client = DynamoClient(table_name=table, region=region)
        self.s3: S3Client = boto3.client("s3", region_name=region)

    # -- source receipt -------------------------------------------------

    def check_receipt(self, merchant: str, image_id: str, rid: int) -> Any:
        """Refuse to render a receipt that no longer belongs to the merchant.

        Receipt ids in the dev table move across re-OCR (the Sprouts finale
        source went from #2 to #1 while #2 became a Vons receipt), so the
        manifest is verified against the live ReceiptPlace before anything
        is rendered. Lists the image's receipts on a mismatch so the fix is
        a one-line manifest edit.
        """
        place = self.client.get_receipt_place(image_id, rid)
        if not _same_merchant(place.merchant_name, merchant):
            others = self.client.get_receipts_from_image(image_id)
            found = []
            for other in others:
                try:
                    name = self.client.get_receipt_place(
                        image_id, other.receipt_id
                    ).merchant_name
                except Exception:  # noqa: BLE001
                    name = "?"
                found.append(f"#{other.receipt_id}={name!r}")
            raise SystemExit(
                f"{image_id}#{rid} is {place.merchant_name!r}, not "
                f"{merchant!r}; receipts on this image: {', '.join(found)}"
            )
        return self.client.get_receipt(image_id, rid)

    def render_final(
        self,
        merchant: str,
        image_id: str,
        rid: int,
        *,
        width: int,
        height: int,
        out_png: str,
    ) -> dict[str, Any]:
        """Production render + render-true labels (glyph_review recipe)."""
        doc = _cached_payload(
            self.cache_dir, self.table, self.region, merchant, image_id, rid
        )
        words = [
            dict(word, _box_index=i) for i, word in enumerate(doc["words"])
        ]
        ss = rsr.section_scale_for_merchant(merchant)
        typ = dict(rsr.merchant_typography(merchant))
        atlas = None
        need_atlas = "bitmap_font" not in typ or (
            self.calibrate_from_corpus and "bitmap_thin" not in typ
        )
        if need_atlas:
            atlas = rsr.cached_glyph_atlas(
                self.table, merchant, region=self.region, max_receipts=8
            )
        prof, typ = resolve_gold_inputs(
            merchant,
            typ,
            table=self.table,
            region=self.region,
            rsr=rsr,
            make_profile=closed_font_profile,
            calibrate_from_corpus=self.calibrate_from_corpus,
            atlas=atlas,
            section_scale=ss,
            canvas_height=height,
            canvas_width=width,
        )
        box_sink: list[dict[str, Any]] = []
        typ["box_sink"] = box_sink
        rsr._render_cached_hybrid(
            {
                "words": words,
                "barcodes": doc["barcodes"],
                "merchant_name": merchant,
            },
            atlas,
            profile=prof,
            width=width,
            height=height,
            path=out_png,
            section_scale=ss,
            **typ,
        )
        return {
            "labels": pa.label_file(
                box_sink,
                words,
                width=width,
                height=height,
                merchant=merchant,
                receipt_key=f"{image_id}#{rid}",
            ),
            "payload": doc,
            "bitmap_font_paths": list((typ.get("bitmap_font") or {}).values()),
        }

    # -- hero-act inputs ------------------------------------------------

    def corpus_stack(
        self, font: str, hero: str, corpus_path: str | None
    ) -> np.ndarray | None:
        if corpus_path is None:
            corpus_path = os.path.join(self.cache_dir, f"{font}.samples.npz")
            if not os.path.exists(corpus_path):
                key = f"merchant_fonts/{font}/corpus.npz"
                try:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    self.s3.download_file(self.corpus_bucket, key, corpus_path)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[export] no letterform corpus at "
                        f"s3://{self.corpus_bucket}/{key} ({exc}); "
                        f"skipping char_prints / char_cloud"
                    )
                    return None
        with np.load(corpus_path, allow_pickle=False) as data:
            key = str(ord(hero))
            if key not in data:
                print(f"[export] corpus has no samples for {hero!r}")
                return None
            return data[key].astype(bool)

    def thumb_receipts(
        self, merchant: str, hero_key: tuple[str, int], count: int
    ) -> list[Any]:
        """The hero receipt first, then the merchant's next receipts.

        Flat SCANs first (they read as receipts at thumbnail size); photos
        only fill remaining slots.
        """
        picked = [self.client.get_receipt(*hero_key)]
        places, _ = self.client.get_receipt_places_by_merchant(merchant)
        keys = sorted(
            {(str(p.image_id), int(p.receipt_id)) for p in places} - {hero_key}
        )
        by_type: dict[str, list[tuple[str, int]]] = {}
        for image_id, rid in keys:
            try:
                image = self.client.get_image(image_id)
            except Exception:  # noqa: BLE001 - skip a drifted row
                continue
            kind = str(getattr(image.image_type, "value", image.image_type))
            by_type.setdefault(kind, []).append((image_id, rid))
        ordered = by_type.pop("SCAN", []) + [
            key for keys_ in by_type.values() for key in keys_
        ]
        for image_id, rid in ordered:
            if len(picked) >= count:
                break
            try:
                picked.append(self.client.get_receipt(image_id, rid))
            except Exception:  # noqa: BLE001 - skip a drifted row
                continue
        return picked


def export_merchant(
    slug: str,
    spec: dict[str, Any],
    exporter: Exporter,
    *,
    out_root: str,
    finale_only: bool,
    corpus_path: str | None,
    logo_override: str | None,
    manifest_path: str | None = None,
    allow_dirty: bool = False,
    commit: str | None = None,
) -> dict[str, Any]:
    merchant = spec["merchant"]
    font = spec["font"]
    hero = spec.get("hero", "A")
    image_id = spec["receipt"]["image_id"]
    rid = int(spec["receipt"]["receipt_id"])
    out_dir = os.path.join(out_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    summary: dict[str, Any] = {"slug": slug, "merchant": merchant}

    receipt = exporter.check_receipt(merchant, image_id, rid)
    width = pa.RECEIPT_WIDTH
    height = pa.receipt_height(receipt.width, receipt.height, width)
    summary["dims"] = {"w": width, "h": height}

    # Finale pair: final.webp + labels, real.webp, logo, compose steps.
    with tempfile.TemporaryDirectory(prefix="pipeline-final-") as tmp:
        png = os.path.join(tmp, "final.png")
        rendered = exporter.render_final(
            merchant, image_id, rid, width=width, height=height, out_png=png
        )
        labels = rendered["labels"]
        payload = rendered["payload"]
        _save_webp(Image.open(png), os.path.join(out_dir, "final.webp"))
    _write_json(os.path.join(out_dir, "final.labels.json"), labels)
    _write_json(
        os.path.join(out_dir, "compose_steps.json"), pa.compose_steps(labels)
    )
    summary["tokens"] = len(labels["tokens"])
    summary["labelled"] = sum(1 for t in labels["ner_tags"] if t != "O")

    scan = load_real_scan(exporter.s3, receipt)
    _save_webp(
        pa.normalize_real(scan, width=width, height=height),
        os.path.join(out_dir, "real.webp"),
    )

    logo_used = False
    if logo_override:
        logo = Image.open(logo_override)
    else:
        logo = rsr._merchant_logo(merchant)
    if logo is None:
        print(
            f"[export] {slug}: no logo in the truth bundle; logo.png skipped"
        )
    else:
        source = logo.convert("L") if logo_override else _alpha_to_gray(logo)
        pa.logo_mask(source).save(os.path.join(out_dir, "logo.png"))
        logo_used = True

    try:
        image = exporter.client.get_image(image_id)
        image_type = str(getattr(image.image_type, "value", image.image_type))
    except Exception:  # noqa: BLE001 - provenance is best-effort on type
        image_type = None
    provenance = card_provenance(
        payload=payload,
        font_paths=rendered["bitmap_font_paths"],
        logo_path=os.path.join(out_dir, "logo.png"),
        logo_used=logo_used,
        final_webp_path=os.path.join(out_dir, "final.webp"),
        image_type=image_type,
        commit=(
            commit
            if commit is not None
            else exporter_commit(_ROOT, allow_dirty=allow_dirty)
        ),
    )
    summary["provenance"] = provenance
    if manifest_path:
        write_manifest_provenance(manifest_path, slug, provenance)

    if finale_only:
        return summary

    font_dir = os.path.join(FONTS_DIR, font)
    if not os.path.isdir(font_dir):
        print(f"[export] {slug}: no font dir {font_dir}; hero acts skipped")
        return summary
    font_json = load_font(font_dir)

    # Act 2: prints + cloud + skeleton + dot params.
    stack = exporter.corpus_stack(font, hero, corpus_path)
    cloud_geom = None
    samples = 0
    if stack is not None:
        samples = int(len(stack))
        prints_dir = os.path.join(out_dir, "char_prints")
        shutil.rmtree(prints_dir, ignore_errors=True)
        os.makedirs(prints_dir)
        prints = pa.char_prints(stack)
        for i, mask in enumerate(prints):
            pa.mask_to_gray(mask).save(os.path.join(prints_dir, f"{i}.png"))
        cloud, cloud_geom = pa.char_cloud(stack)
        cloud.save(os.path.join(out_dir, "char_cloud.png"))
        summary["char_prints"] = len(prints)
    skeleton_src = os.path.join(font_dir, "glyphs", glyph_filename(ord(hero)))
    if os.path.exists(skeleton_src):
        shutil.copyfile(
            skeleton_src, os.path.join(out_dir, "char_skeleton.json")
        )
    else:
        print(f"[export] {slug}: no skeleton for {hero!r} at {skeleton_src}")
    _write_json(
        os.path.join(out_dir, "dot_params.json"),
        pa.dot_params(
            font_json, hero=hero, samples=samples, cloud_geom=cloud_geom
        ),
    )

    # Act 3: the renderer's own glyph masks from a fresh compile.
    with tempfile.TemporaryDirectory(prefix="pipeline-font-") as tmp:
        npz = os.path.join(tmp, f"{font}.glyphs.npz")
        compile_font(font_dir, npz)
        masks, metrics = pa.font_grid(BitmapFont(npz).glyph)
    grid_dir = os.path.join(out_dir, "font_grid")
    shutil.rmtree(grid_dir, ignore_errors=True)
    os.makedirs(grid_dir)
    for cp, mask in masks.items():
        pa.mask_to_rgba(mask).save(os.path.join(grid_dir, f"{cp}.png"))
    _write_json(os.path.join(out_dir, "font_metrics.json"), metrics)
    summary["font_grid"] = len(masks)

    # Measured style: notable sections + crops from the real scan.
    stylemap_path = os.path.join(font_dir, "stylemap.json")
    if os.path.exists(stylemap_path):
        with open(stylemap_path, encoding="utf-8") as fh:
            stylemap = json.load(fh)
        doc = _cached_payload(
            exporter.cache_dir,
            exporter.table,
            exporter.region,
            merchant,
            image_id,
            rid,
        )
        notable = [
            name
            for name, sec in (stylemap.get("sections") or {}).items()
            if isinstance(sec, dict) and pa.style_display(sec) != "Body text"
        ]
        matches = {
            name: sec["match"]
            for name, sec in (stylemap.get("sections") or {}).items()
            if isinstance(sec, dict) and sec.get("match")
        }
        boxes = pa.style_crop_boxes(
            doc["words"],
            lambda text: _classify(text, line_has_price(text.split()), font),
            notable,
            matches=matches,
        )
        crops_dir = os.path.join(out_dir, "style_crops")
        shutil.rmtree(crops_dir, ignore_errors=True)
        crops: dict[str, str] = {}
        for name, box in boxes.items():
            os.makedirs(crops_dir, exist_ok=True)
            pa.crop_receipt(scan, box).save(
                os.path.join(crops_dir, f"{name}.png")
            )
            crops[name] = f"style_crops/{name}.png"
        _write_json(
            os.path.join(out_dir, "style_annotated.json"),
            pa.style_annotated(stylemap, merchant=merchant, crops=crops),
        )
        summary["style_sections"] = len(notable)

    # Act 1: real thumbnails.
    thumbs_dir = os.path.join(out_dir, "real_thumbs")
    shutil.rmtree(thumbs_dir, ignore_errors=True)
    os.makedirs(thumbs_dir)
    for i, thumb_receipt in enumerate(
        exporter.thumb_receipts(merchant, (image_id, rid), pa.REAL_THUMB_COUNT)
    ):
        thumb_scan = (
            scan
            if (thumb_receipt.image_id, thumb_receipt.receipt_id)
            == (image_id, rid)
            else load_real_scan(exporter.s3, thumb_receipt)
        )
        _save_webp(
            pa.thumbnail(thumb_scan), os.path.join(thumbs_dir, f"{i}.webp")
        )
    return summary


def _alpha_to_gray(rgba: Image.Image) -> Image.Image:
    """The renderer's logo (alpha = ink) back to black-on-white grey."""
    alpha = np.asarray(rgba.convert("RGBA"))[..., 3]
    return Image.fromarray((255 - alpha).astype(np.uint8), "L")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("slugs", nargs="*", help="portfolio merchant slug(s)")
    ap.add_argument("--all", action="store_true", help="every manifest slug")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument(
        "--finale-only",
        action="store_true",
        help="only final/real/labels/logo/compose_steps",
    )
    ap.add_argument(
        "--corpus", default=None, help="local *.samples.npz instead of S3"
    )
    ap.add_argument(
        "--logo", default=None, help="black-on-white PNG overriding the vault"
    )
    ap.add_argument(
        "--table", default=os.environ.get("DYNAMODB_TABLE_NAME", DEFAULT_TABLE)
    )
    ap.add_argument(
        "--region", default=os.environ.get("AWS_REGION", "us-east-1")
    )
    ap.add_argument(
        "--cache-dir", default=os.path.join(_ROOT, ".out", "merchant_gold")
    )
    ap.add_argument(
        "--calibrate-from-corpus",
        action="store_true",
        help="authoring: rebuild cached_font_profile(n=12) and live bitmap_thin",
    )
    ap.add_argument(
        "--allow-dirty",
        action="store_true",
        help="record {sha}-dirty in provenance instead of refusing a dirty HEAD",
    )
    ap.add_argument(
        "--exporter-commit",
        default=None,
        help=(
            "provenance SHA already validated by the caller; "
            "do not recheck the worktree"
        ),
    )
    args = ap.parse_args(argv)

    manifest = load_manifest(args.manifest)
    slugs = list(manifest) if args.all else list(args.slugs)
    if not slugs:
        ap.error("give at least one slug or --all")
    unknown = [s for s in slugs if s not in manifest]
    if unknown:
        ap.error(f"unknown slug(s) {unknown}; known: {sorted(manifest)}")
    if len(slugs) > 1 and (args.corpus or args.logo):
        ap.error("--corpus/--logo apply to a single slug")

    # One check for the whole run. Writing provenance into the tracked
    # manifest dirties HEAD, so a per-merchant recheck fails the next slug
    # in --all / a multi-slug argv. Callers that already validated (new_vendor
    # export, before set_entry) pass --exporter-commit.
    if args.exporter_commit is not None:
        commit = args.exporter_commit
    else:
        commit = exporter_commit(_ROOT, allow_dirty=args.allow_dirty)

    exporter = Exporter(
        table=args.table,
        region=args.region,
        cache_dir=args.cache_dir,
        corpus_bucket=os.environ.get(CORPUS_BUCKET_ENV, CORPUS_BUCKET_DEFAULT),
        calibrate_from_corpus=args.calibrate_from_corpus,
    )
    summaries = []
    for slug in slugs:
        summary = export_merchant(
            slug,
            manifest[slug],
            exporter,
            out_root=args.out_dir,
            finale_only=args.finale_only,
            corpus_path=args.corpus,
            logo_override=args.logo,
            manifest_path=args.manifest,
            commit=commit,
        )
        summaries.append(summary)
        print(f"[export] {slug}: {json.dumps(summary, sort_keys=True)}")
    print("RECEIPT_DIMS entries for pipelineData.ts:")
    for summary in summaries:
        dims = summary["dims"]
        print(f"  {summary['slug']}: {{ w: {dims['w']}, h: {dims['h']} }},")
    return 0


if __name__ == "__main__":
    sys.exit(main())
