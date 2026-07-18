#!/usr/bin/env python3
"""Typography line-crop packets for blind per-line labeling.

For each merchant: OCR-vetted receipts (``ocr_overlap_score`` <= 2, the M3
rule) -> identical pilot visual-line grouping and letter cleaning -> raw
grayscale line and letter crops.  Receipt-wide size/weight tiers are measured
before packet caps, then a deterministic manifest and merchant-blind judge
inputs are published.

READ-ONLY against DynamoDB and S3: this command performs only the reads in
``_load_words_and_real``, ``get_image_details``, and
``get_receipt_places_by_merchant``.  It never writes to AWS.

Usage:
  python typography_packets.py \
      --merchant "Sprouts Farmers Market:sprouts" \
      --merchant "Wild Fork:wildfork" \
      [--receipts 2] [--max-lines 25] [--min-letters 4] \
      [--max-overlaps 2] [--contamination 0.20] \
      [--out-dir ../../../.out/typography_packets] [--table ...]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import PurePosixPath
from statistics import median

import numpy as np
from PIL import Image

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

from glyphstudio.packets import (  # noqa: E402
    build_manifest,
    canonical_json,
    judge_input,
    packet_id,
    write_manifest,
)
from glyphstudio.stylescan import (  # noqa: E402
    _run_widths,
    group_visual_lines,
    reverse_video_probe,
    underline_probe,
)
from glyphstudio.typography import (  # noqa: E402
    assign_tiers,
    clean_letter_mask,
    estimate_slant,
    intra_line_overlap,
    line_slant,
)
from m3_acceptance import ocr_overlap_score  # noqa: E402

DEFAULT_MERCHANTS = ["Wild Fork:wildfork", "Sprouts Farmers Market:sprouts"]
CONTAMINATION_MAX = 0.20
MIN_LETTERS = 4


def _extract_receipt(
    client,
    merchant: str,
    image_id: str,
    receipt_id: int,
    max_overlaps: int,
) -> dict:
    """Load, vet, and measure one receipt using the typography pilot rules."""
    from glyph_segment import auto_polarity, sauvola_mask
    from receipt_line_scorecard import _load_words_and_real

    real, words = _load_words_and_real(merchant, image_id, receipt_id)
    overlaps = ocr_overlap_score(words)
    if overlaps > max_overlaps:
        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "ocr_overlap_score": overlaps,
            "vetted_out": True,
        }

    gray = np.asarray(real.convert("L"))
    height, width = gray.shape
    details = client.get_image_details(image_id)
    letters = [
        letter
        for letter in details.receipt_letters
        if str(letter.receipt_id) == str(receipt_id)
    ]

    words_px = []
    for word in words:
        x0, y0, x1, y1 = word["bbox"]
        left = min(x0, x1) / 1000 * width
        right = max(x0, x1) / 1000 * width
        top = (1 - max(y0, y1) / 1000) * height
        bottom = (1 - min(y0, y1) / 1000) * height
        words_px.append(
            {
                "text": word["text"],
                "line_id": word.get("line_id"),
                "l": left,
                "r": right,
                "t": top,
                "b": bottom,
                "cy": (top + bottom) / 2,
                "h": bottom - top,
            }
        )
    visual_lines = group_visual_lines(words_px)

    letters_by_line: dict[int, list] = defaultdict(list)
    for letter in letters:
        letters_by_line[int(letter.line_id)].append(letter)

    def box_px(obj) -> tuple[float, float, float, float]:
        top_left, bottom_right = obj.top_left, obj.bottom_right
        return (
            min(top_left["x"], bottom_right["x"]) * width,
            (1 - max(top_left["y"], bottom_right["y"])) * height,
            max(top_left["x"], bottom_right["x"]) * width,
            (1 - min(top_left["y"], bottom_right["y"])) * height,
        )

    line_records = []
    for index, line in enumerate(visual_lines):
        line.sort(key=lambda word: word["l"])
        line_top = min(word["t"] for word in line)
        line_bottom = max(word["b"] for word in line)
        line_left = min(word["l"] for word in line)
        line_right = max(word["r"] for word in line)
        line_ids = sorted(
            {
                int(word["line_id"])
                for word in line
                if word["line_id"] is not None
            }
        )
        boxes = []
        caps = []
        strokes = []
        densities = []
        accepted_letters = []
        glyph_slants = []
        for line_id in line_ids:
            for letter in letters_by_line[line_id]:
                x0, y0, x1, y1 = box_px(letter)
                if not (y0 >= line_top - 5 and y1 <= line_bottom + 5):
                    continue
                xi0, yi0 = max(0, int(x0)), max(0, int(y0))
                xi1 = min(width, int(x1) + 1)
                yi1 = min(height, int(y1) + 1)
                if xi1 - xi0 < 3 or yi1 - yi0 < 3:
                    continue
                char = str(letter.text or "")[:1]
                if not char.strip():
                    continue
                boxes.append((x0, y0, x1, y1))
                raw_crop = gray[yi0:yi1, xi0:xi1]
                polarity_crop, _ = auto_polarity(raw_crop)
                mask = clean_letter_mask(sauvola_mask(polarity_crop))
                if mask.sum() < 8:
                    continue
                ys, _ = np.where(mask)
                ink_height = int(ys.max() - ys.min() + 1)
                if char.isupper() or char.isdigit():
                    caps.append(float(ink_height))
                runs = _run_widths(mask)
                if runs:
                    strokes.append(float(np.mean(runs)))
                densities.append(float(mask.mean()))
                glyph_slants.append(
                    (
                        char,
                        (
                            estimate_slant(mask)
                            if ink_height >= 8
                            else float("nan")
                        ),
                    )
                )
                accepted_letters.append(
                    {
                        "char": char,
                        "line_id": line_id,
                        "crop": raw_crop.copy(),
                        "box": (x0, y0, x1, y1),
                    }
                )

        accepted_letters.sort(
            key=lambda item: (
                item["box"][0],
                item["box"][1],
                item["box"][2],
                item["line_id"],
            )
        )
        slant = line_slant(glyph_slants)
        line_crop = gray[
            max(0, int(line_top) - 3) : int(line_bottom) + 4,
            max(0, int(line_left) - 3) : int(line_right) + 4,
        ].copy()
        line_records.append(
            {
                "index": index,
                "text": " ".join(word["text"] for word in line),
                "line_ids": line_ids,
                "cap_px": round(median(caps), 1) if caps else None,
                "stroke_med": (round(median(strokes), 2) if strokes else None),
                "density_med": (
                    round(median(densities), 4) if densities else None
                ),
                "n_letters": len(accepted_letters),
                "contamination": round(intra_line_overlap(boxes), 3),
                "underline": bool(
                    underline_probe(
                        gray,
                        line_top,
                        line_bottom,
                        line_left,
                        line_right,
                    )
                ),
                "reverse_video": int(
                    reverse_video_probe(
                        gray,
                        line_top,
                        line_bottom,
                        line_left,
                        line_right,
                    )
                ),
                "slant_deg": round(slant, 1) if slant is not None else None,
                "line_crop": line_crop,
                "letters": accepted_letters,
            }
        )
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "image_size": [width, height],
        "ocr_overlap_score": overlaps,
        "vetted_out": False,
        "lines": line_records,
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png_bytes(crop: np.ndarray) -> bytes:
    buffer = BytesIO()
    image = Image.fromarray(np.asarray(crop, dtype=np.uint8))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_immutable_bytes(path: str, payload: bytes) -> None:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            existing = fh.read()
        if existing != payload:
            raise RuntimeError(f"immutable packet artifact differs: {path}")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "xb") as fh:
        fh.write(payload)


def _write_crop(
    out_dir: str, relpath: PurePosixPath, crop: np.ndarray
) -> dict:
    payload = _png_bytes(crop)
    path = os.path.join(out_dir, *relpath.parts)
    _write_immutable_bytes(path, payload)
    height, width = crop.shape
    return {
        "path": relpath.as_posix(),
        "sha256": _sha256(payload),
        "width": int(width),
        "height": int(height),
    }


def _packet_record(
    out_dir: str,
    merchant: str,
    slug: str,
    receipt: dict,
    line: dict,
    body_cap,
    body_stroke,
) -> dict:
    image_id = str(receipt["image_id"])
    receipt_id = int(receipt["receipt_id"])
    line_index = int(line["index"])
    pid = packet_id(image_id, receipt_id, line_index)
    base = PurePosixPath("packets", pid)
    line_record = _write_crop(
        out_dir, base / "line.png", np.asarray(line["line_crop"])
    )
    letter_records = []
    for seq, letter in enumerate(line["letters"]):
        char = str(letter["char"])[:1]
        relpath = base / "letters" / f"{seq:03d}_u{ord(char):04x}.png"
        crop_record = _write_crop(out_dir, relpath, np.asarray(letter["crop"]))
        letter_records.append(
            {
                "seq": seq,
                "char": char,
                "line_id": int(letter["line_id"]),
                **crop_record,
            }
        )
    return {
        "packet_id": pid,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "line_index": line_index,
        "line_ids": [int(value) for value in line["line_ids"]],
        "merchant": merchant,
        "slug": slug,
        "text": line["text"],
        "features": {
            key: line[key]
            for key in (
                "cap_px",
                "stroke_med",
                "density_med",
                "n_letters",
                "contamination",
                "underline",
                "reverse_video",
                "slant_deg",
                "tier",
            )
        },
        "receipt_context": {
            "body_cap_px": float(body_cap) if body_cap is not None else None,
            "body_stroke_px": (
                float(body_stroke) if body_stroke is not None else None
            ),
            "image_size": [int(value) for value in receipt["image_size"]],
            "ocr_overlap_score": int(receipt["ocr_overlap_score"]),
        },
        "crops": {"line": line_record, "letters": letter_records},
    }


def _merchant_pairs(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        name, _, slug = value.partition(":")
        slug = slug or name.lower().replace(" ", "")
        pairs.append((name, slug))
    return pairs


def _write_judge_inputs(manifest: dict, out_dir: str) -> str:
    filename = f"judge_inputs_{manifest['content_hash'][:16]}.json"
    path = os.path.join(out_dir, filename)
    payload = (
        canonical_json([judge_input(packet) for packet in manifest["packets"]])
        + "\n"
    ).encode("ascii")
    _write_immutable_bytes(path, payload)
    return path


def generate_packets(
    client, merchant_values: list[str], args
) -> tuple[dict, str]:
    """Extract and publish one deterministic packet set for all merchants."""
    packets = []
    summaries = {}
    for merchant, slug in _merchant_pairs(merchant_values):
        print(f"\n===== {merchant} ({slug}) =====")
        places, _ = client.get_receipt_places_by_merchant(
            merchant_name=merchant, limit=60
        )
        receipts = []
        vetted_out = 0
        for place in places:
            if len(receipts) >= args.receipts:
                break
            image_id = str(place.image_id)
            receipt_id = int(place.receipt_id)
            try:
                receipt = _extract_receipt(
                    client,
                    merchant,
                    image_id,
                    receipt_id,
                    args.max_overlaps,
                )
            except Exception as exc:  # noqa: BLE001 - skip one bad receipt
                print(f"  [skip] {image_id[:8]}#{receipt_id}: {exc}")
                continue
            if receipt.get("vetted_out"):
                vetted_out += 1
                print(
                    f"  [vet] {image_id[:8]}#{receipt_id}: "
                    f"overlap={receipt['ocr_overlap_score']} > "
                    f"{args.max_overlaps}"
                )
                continue
            receipts.append(receipt)
            print(
                f"  [ok] {image_id[:8]}#{receipt_id}: "
                f"{len(receipt['lines'])} lines, "
                f"{sum(line['n_letters'] for line in receipt['lines'])} "
                "letters"
            )

        merchant_packets = []
        measured_lines = 0
        measured_letters = 0
        contaminated = 0
        misgrouped = 0
        for receipt in receipts:
            lines = receipt["lines"]
            body_cap, body_stroke = assign_tiers(lines)
            measured_lines += len(lines)
            measured_letters += sum(line["n_letters"] for line in lines)
            for line in sorted(lines, key=lambda item: item["index"]):
                if line["n_letters"] < args.min_letters:
                    continue
                # A crop much taller than its own cap height means the
                # y-center grouping merged several physical lines (seen in
                # Costco b1: one 5-line crop reached a judge). Never packet
                # such crops; count and report them.
                cap = line.get("cap_px")
                crop_h = line["line_crop"].shape[0]
                if cap and crop_h > args.max_crop_cap_ratio * cap:
                    misgrouped += 1
                    print(
                        f"  [misgroup] {receipt['image_id'][:8]}"
                        f"#{receipt['receipt_id']} line {line['index']}: "
                        f"crop_h={crop_h} > "
                        f"{args.max_crop_cap_ratio:g}x cap={cap}"
                    )
                    continue
                if len(merchant_packets) >= args.max_lines:
                    continue
                record = _packet_record(
                    args.out_dir,
                    merchant,
                    slug,
                    receipt,
                    line,
                    body_cap,
                    body_stroke,
                )
                merchant_packets.append(record)
                contaminated += int(line["contamination"] > args.contamination)
        packets.extend(merchant_packets)
        summaries[slug] = {
            "merchant": merchant,
            "accepted": len(receipts),
            "vetted_out": vetted_out,
            "misgrouped_rejected": misgrouped,
            "packets": len(merchant_packets),
            "lines": measured_lines,
            "letters": measured_letters,
            "packet_letters": sum(
                packet["features"]["n_letters"] for packet in merchant_packets
            ),
            "contaminated": contaminated,
            "tiers": Counter(
                packet["features"]["tier"] for packet in merchant_packets
            ),
            "underline": Counter(
                str(packet["features"]["underline"]).lower()
                for packet in merchant_packets
            ),
            "reverse": Counter(
                str(packet["features"]["reverse_video"])
                for packet in merchant_packets
            ),
            "slant": Counter(
                (
                    "none"
                    if packet["features"]["slant_deg"] is None
                    else str(packet["features"]["slant_deg"])
                )
                for packet in merchant_packets
            ),
        }

    params = {
        "receipts": args.receipts,
        "max_lines": args.max_lines,
        "min_letters": args.min_letters,
        "max_overlaps": args.max_overlaps,
        "contamination_max": args.contamination,
        "max_crop_cap_ratio": args.max_crop_cap_ratio,
        "table": args.table,
        "merchants": [
            f"{merchant}:{slug}"
            for merchant, slug in _merchant_pairs(merchant_values)
        ],
    }
    manifest = build_manifest(packets, params)
    manifest_path = write_manifest(manifest, args.out_dir)
    _write_judge_inputs(manifest, args.out_dir)

    for slug, summary in summaries.items():
        print(
            f"  [{slug}] receipts={summary['accepted']} "
            f"vetted_out={summary['vetted_out']} packets={summary['packets']} "
            f"lines={summary['lines']} letters={summary['letters']} "
            f"packet_letters={summary['packet_letters']} "
            f"contaminated_included={summary['contaminated']}"
        )
        print(
            f"    tiers={dict(sorted(summary['tiers'].items()))} "
            f"underline={dict(sorted(summary['underline'].items()))} "
            f"reverse={dict(sorted(summary['reverse'].items()))} "
            f"slant={dict(sorted(summary['slant'].items()))}"
        )
    print(f"manifest={manifest_path}")
    print(f"content_hash={manifest['content_hash']}")
    return manifest, manifest_path


def _nonnegative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _fraction(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merchant", action="append", default=None)
    parser.add_argument("--receipts", type=_nonnegative, default=2)
    parser.add_argument("--max-lines", type=_nonnegative, default=25)
    parser.add_argument(
        "--min-letters", type=_nonnegative, default=MIN_LETTERS
    )
    parser.add_argument("--max-overlaps", type=_nonnegative, default=2)
    parser.add_argument(
        "--contamination", type=_fraction, default=CONTAMINATION_MAX
    )
    parser.add_argument("--max-crop-cap-ratio", type=float, default=3.0)
    parser.add_argument(
        "--out-dir",
        default=os.path.join(_ROOT, ".out", "typography_packets"),
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    args = parser.parse_args(argv)
    os.environ["DYNAMODB_TABLE_NAME"] = args.table

    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(args.table)
    generate_packets(client, args.merchant or DEFAULT_MERCHANTS, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
