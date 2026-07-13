#!/usr/bin/env python3
"""Label-preserving re-OCR migration: apply #1095 segmentation to existing images.

Per image: run the new Swift OCR on the LOCAL raw image, build the new receipt
hierarchy (same conventions as the ingestion lambda's
``_parse_receipt_ocr_from_swift``: ids ``enumerate(start=1)``, empty-text /
zero-confidence skip rules), MOVE every human label onto its matched new word,
then delete the stale rows — children first, never ``delete_image_details``.

Label policy (the point of this tool):
  - MOVE, never regenerate: a matched label keeps ``label``,
    ``validation_status``, ``label_proposed_by``, ``label_consolidated_from``,
    ``reasoning`` and ``timestamp_added`` verbatim — the audit trail survives.
  - Matching = old receipt -> new receipt correspondence (word-text Jaccard),
    then per-receipt one-to-one word matching by combined TEXT similarity +
    CENTROID proximity (measured 97.8% auto-preservation on 616 dev images).
  - PARK, never drop: an unmatched label is attached to the nearest unconsumed
    new word with ``validation_status=NEEDS_REVIEW`` and a reasoning note that
    records the original status/word — zero labels are ever deleted.
  - Labels already orphaned BEFORE the migration (their word row is missing in
    dev) are left completely untouched and reported; they are pre-existing
    breakage for the separate cleanup pass.
  - No machine (LayoutLM) labels are written during migration: moved human
    labels are the only labels this tool produces, so a human label can never
    be shadowed by a machine proposal (the normal pipeline can re-propose
    later).

Safety:
  - Local-first: refuses non-local endpoints unless ``--allow-aws``.
  - Requires a rollback ``backup`` (from ocr_migration_rehearsal.py) covering
    every target image, taken from the SAME endpoint, before it writes.
  - ``--dry-run`` computes and reports everything, writes nothing.

Out of scope here (dev-phase steps, by design): crop upload + CDN formats +
CloudFront invalidation, OCRJob bookkeeping, embedding kick-off. The new
RECEIPT_WORD/LINE rows default to ``embedding_status=NONE`` so the batch
embedding step functions discover them.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "receipt_dynamo"))
from receipt_dynamo.entities import (  # noqa: E402
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
)
from scripts import ocr_migration_rehearsal as reh  # noqa: E402

LOG = logging.getLogger("ocr_migration_apply")

MIN_SCORE, W_TEXT, W_POS, POS_SCALE = 0.55, 0.5, 0.5, 0.5

# Old receipt-scoped SKs to delete after a successful re-OCR. Dead analysis
# families are dropped on purpose (no producers/consumers; see game plan).
STALE_SK_PREFIX = "RECEIPT#"
PRESERVED_TYPES = {"RECEIPT_PLACE"}  # re-keyed, not deleted-and-lost


def _norm(t: str | None) -> str:
    return " ".join((t or "").strip().upper().split())


def _centroid(d: dict[str, Any]) -> tuple[float, float]:
    # float() everything: rows read live from DynamoDB deserialize numbers as
    # decimal.Decimal, and Decimal / float raises TypeError.
    # Older-era word rows can lack corner attrs; fall back to bounding_box so
    # their labels are MOVED/PARKED instead of mis-classed as pre-orphans while
    # their words get deleted (live-dev wave-2 halt, image 1b441d33).
    if "top_left" in d and "bottom_right" in d:
        return (
            (float(d["top_left"]["x"]) + float(d["bottom_right"]["x"])) / 2.0,
            (float(d["top_left"]["y"]) + float(d["bottom_right"]["y"])) / 2.0,
        )
    bb = d["bounding_box"]
    return (
        float(bb["x"]) + float(bb["width"]) / 2.0,
        float(bb["y"]) + float(bb["height"]) / 2.0,
    )


def _score(
    text_a: str, ca: tuple[float, float], text_b: str, cb: tuple[float, float]
) -> float:
    ts = SequenceMatcher(None, text_a, text_b).ratio()
    ps = max(0.0, 1.0 - math.hypot(ca[0] - cb[0], ca[1] - cb[1]) / POS_SCALE)
    return W_TEXT * ts + W_POS * ps


# --------------------------------------------------------------------------- #
# Planning (pure, unit-testable)                                              #
# --------------------------------------------------------------------------- #


@dataclass
class LabelMove:
    old_key: tuple[int, int, int]  # (receipt_id, line_id, word_id)
    new_key: tuple[int, int, int]
    native: dict[str, Any]  # the old label row's native attributes
    label: str


@dataclass
class LabelPark:
    old_key: tuple[int, int, int]
    new_key: tuple[int, int, int]  # best-guess word the parked label attaches to
    native: dict[str, Any]
    label: str
    original_status: str


@dataclass
class MovePlan:
    moves: list[LabelMove] = field(default_factory=list)
    parks: list[LabelPark] = field(default_factory=list)
    pre_orphaned: list[tuple[tuple[int, int, int], str]] = field(default_factory=list)


def correspond_receipts(
    old_words: dict[tuple[int, int, int], dict[str, Any]],
    new_words: dict[tuple[int, int, int], dict[str, Any]],
) -> dict[int, int]:
    """old receipt_id -> new receipt_id by word-text Jaccard overlap."""
    old_sets: dict[int, Counter] = defaultdict(Counter)
    new_sets: dict[int, Counter] = defaultdict(Counter)
    for (r, _l, _w), d in old_words.items():
        old_sets[r][d["text"]] += 1
    for (r, _l, _w), d in new_words.items():
        new_sets[r][d["text"]] += 1
    pairs = []
    for orid, oset in old_sets.items():
        for nrid, nset in new_sets.items():
            inter = sum((oset & nset).values())
            union = sum((oset | nset).values()) or 1
            pairs.append((inter / union, orid, nrid))
    pairs.sort(reverse=True)
    used_new: set[int] = set()
    corr: dict[int, int] = {}
    for s, orid, nrid in pairs:
        if orid in corr or nrid in used_new or s <= 0:
            continue
        corr[orid] = nrid
        used_new.add(nrid)
    return corr


def plan_label_moves(
    old_words: dict[tuple[int, int, int], dict[str, Any]],
    old_labels: dict[tuple[int, int, int], list[dict[str, Any]]],
    new_words: dict[tuple[int, int, int], dict[str, Any]],
) -> MovePlan:
    """Decide, for every old label, where it lands in the new hierarchy.

    ``old_words``/``new_words``: key -> {"text": norm, "c": (x, y)}.
    ``old_labels``: old word key -> [label-row native dicts].
    One physical word may carry several labels; they all ride its match.
    """
    plan = MovePlan()
    corr = correspond_receipts(old_words, new_words)
    consumed: set[tuple[int, int, int]] = set()

    # Pre-orphans: label rows whose word does not exist — leave untouched.
    labeled_keys = []
    for okey, rows in old_labels.items():
        if okey not in old_words:
            for row in rows:
                plan.pre_orphaned.append((okey, str(row.get("label", ""))))
        else:
            labeled_keys.append(okey)

    # Greedy best-score matching per corresponded receipt.
    cands = []
    for okey in labeled_keys:
        od = old_words[okey]
        nrid = corr.get(okey[0])
        if nrid is None:
            continue
        for nkey, nd in new_words.items():
            if nkey[0] != nrid:
                continue
            s = _score(od["text"], od["c"], nd["text"], nd["c"])
            if s >= MIN_SCORE:
                cands.append((s, okey, nkey))
    cands.sort(key=lambda x: x[0], reverse=True)
    matched: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for s, okey, nkey in cands:
        if okey in matched or nkey in consumed:
            continue
        matched[okey] = nkey
        consumed.add(nkey)

    # Fallback pass: unmatched labeled words try any unconsumed new word.
    for okey in labeled_keys:
        if okey in matched:
            continue
        od = old_words[okey]
        best, best_s = None, 0.0
        for nkey, nd in new_words.items():
            if nkey in consumed:
                continue
            s = _score(od["text"], od["c"], nd["text"], nd["c"])
            if s > best_s:
                best, best_s = nkey, s
        if best is not None and best_s >= MIN_SCORE:
            matched[okey] = best
            consumed.add(best)

    # (new word key, label) pairs already claimed — a label row's PK/SK is
    # unique per (word, label), so two labels landing on the same pair would be
    # DUPLICATE KEYS in the batch write (this killed the first real apply on
    # images with repeated labels, e.g. many ADDRESS_LINE rows).
    taken: set[tuple[tuple[int, int, int], str]] = set()

    # PASS 1 — finalize all MOVES first so their (word, label) slots are
    # claimed before any park chooses a landing spot. (A park's all-consumed
    # fallback may land on a move's word; if the park ran first it could claim
    # the same (word, label) a later move needs -> duplicate PK/SK.)
    for okey in labeled_keys:
        if okey not in matched:
            continue
        rows = old_labels[okey]
        for row in rows:
            lab = str(row.get("label", ""))
            plan.moves.append(LabelMove(okey, matched[okey], row, lab))
            taken.add((matched[okey], lab))

    # PASS 2 — parks.
    for okey in labeled_keys:
        rows = old_labels[okey]
        labels_here = [str(row.get("label", "")) for row in rows]
        if okey in matched:
            pass  # handled in pass 1
        else:
            # PARK: nearest word (unconsumed first) whose (word, label) slots
            # are all free. Consume it so parked labels spread out instead of
            # piling onto one word.
            od = old_words[okey]

            def _nearest(pool: Iterable[tuple[int, int, int]]):
                best, best_d = None, float("inf")
                for nkey in pool:
                    nd = new_words[nkey]
                    if any((nkey, lab) in taken for lab in labels_here):
                        continue
                    d = math.hypot(od["c"][0] - nd["c"][0], od["c"][1] - nd["c"][1])
                    if d < best_d:
                        best, best_d = nkey, d
                return best

            best = _nearest(k for k in new_words if k not in consumed)
            if best is None:  # all consumed — any word with free label slots
                best = _nearest(new_words.keys())
            if best is None:
                # Pathological (labels with this name outnumber words). Refuse
                # rather than lose or collide; the image errors out cleanly
                # before any write happens.
                raise RuntimeError(
                    f"cannot place parked label(s) {labels_here} from {okey}: "
                    "no (word, label) slot left"
                )
            consumed.add(best)
            for row, lab in zip(rows, labels_here):
                plan.parks.append(
                    LabelPark(
                        okey,
                        best,
                        row,
                        lab,
                        str(row.get("validation_status", "NONE")),
                    )
                )
                taken.add((best, lab))
    return plan


def moved_label_entity(
    image_id: str, move: LabelMove, parked: bool = False, old_word_text: str = ""
) -> ReceiptWordLabel:
    """Build the re-keyed label entity, carrying the audit trail verbatim."""
    n = move.native
    rid, lid, wid = move.new_key
    reasoning = n.get("reasoning")
    status = n.get("validation_status")
    if parked:
        note = (
            f"[PARKED by re-OCR migration: no confident word match; "
            f"original status={status or 'NONE'}; original word "
            f"'{old_word_text}' @ {move.old_key}]"
        )
        reasoning = f"{note} {reasoning}" if reasoning else note
        status = "NEEDS_REVIEW"
    return ReceiptWordLabel(
        image_id=image_id,
        receipt_id=rid,
        line_id=lid,
        word_id=wid,
        label=move.label,
        reasoning=reasoning,
        timestamp_added=n.get("timestamp_added"),
        validation_status=status,
        label_proposed_by=n.get("label_proposed_by"),
        label_consolidated_from=n.get("label_consolidated_from"),
    )


# --------------------------------------------------------------------------- #
# OCR + entity construction (ingestion conventions)                            #
# --------------------------------------------------------------------------- #


def run_swift_ocr(binary: Path, image_path: Path) -> dict[str, Any] | None:
    out = tempfile.mkdtemp()
    subprocess.run(
        [
            str(binary),
            "--process-local-image",
            str(image_path),
            "--output-dir",
            out,
            "--log-level",
            "error",
        ],
        capture_output=True,
        timeout=300,
    )
    js = [f for f in os.listdir(out) if f.endswith(".json") and "RECEIPT" not in f]
    return json.load(open(os.path.join(out, js[0]))) if js else None


def _valid_geometry(d: dict[str, Any]) -> bool:
    bbox = d.get("bounding_box", {})
    if not all(k in bbox for k in ("x", "y", "width", "height")):
        return False
    return all(
        all(k in d.get(c, {}) for k in ("x", "y"))
        for c in ("top_left", "top_right", "bottom_left", "bottom_right")
    )


def build_new_entities(
    image_id: str, ocr: dict[str, Any], raw_bucket: str, timestamp: str
) -> tuple[list[Receipt], list[ReceiptLine], list[ReceiptWord], list[ReceiptLetter]]:
    """Swift OCR JSON -> entities, mirroring _parse_receipt_ocr_from_swift."""
    receipts, lines, words, letters = [], [], [], []
    for receipt_idx, rc in enumerate(ocr.get("receipts") or [], start=1):
        bounds = rc.get("bounds") or {}
        raw_key = f"receipts/{image_id}/{image_id}_RECEIPT_{receipt_idx:05d}.png"
        receipts.append(
            Receipt(
                image_id=image_id,
                receipt_id=receipt_idx,
                width=int(rc.get("warped_width", 0)),
                height=int(rc.get("warped_height", 0)),
                timestamp_added=timestamp,
                raw_s3_bucket=raw_bucket,
                raw_s3_key=raw_key,
                top_left=bounds.get("top_left"),
                top_right=bounds.get("top_right"),
                bottom_left=bounds.get("bottom_left"),
                bottom_right=bounds.get("bottom_right"),
            )
        )
        for line_idx, ld in enumerate(rc.get("lines") or [], start=1):
            if not ld.get("text") or not _valid_geometry(ld):
                continue
            lines.append(
                ReceiptLine(
                    image_id=image_id,
                    receipt_id=receipt_idx,
                    line_id=line_idx,
                    text=ld["text"],
                    bounding_box=ld["bounding_box"],
                    top_left=ld["top_left"],
                    top_right=ld["top_right"],
                    bottom_left=ld["bottom_left"],
                    bottom_right=ld["bottom_right"],
                    angle_degrees=ld.get("angle_degrees", 0.0),
                    angle_radians=ld.get("angle_radians", 0.0),
                    confidence=ld.get("confidence", 1.0),
                )
            )
            for word_idx, wd in enumerate(ld.get("words") or [], start=1):
                if (
                    not wd.get("text")
                    or wd.get("confidence", 0.0) <= 0.0
                    or not _valid_geometry(wd)
                ):
                    continue
                words.append(
                    ReceiptWord(
                        image_id=image_id,
                        receipt_id=receipt_idx,
                        line_id=line_idx,
                        word_id=word_idx,
                        text=wd["text"],
                        bounding_box=wd["bounding_box"],
                        top_left=wd["top_left"],
                        top_right=wd["top_right"],
                        bottom_left=wd["bottom_left"],
                        bottom_right=wd["bottom_right"],
                        angle_degrees=wd.get("angle_degrees", 0.0),
                        angle_radians=wd.get("angle_radians", 0.0),
                        confidence=wd["confidence"],
                        extracted_data=wd.get("extracted_data"),
                    )
                )
                for letter_idx, lt in enumerate(wd.get("letters") or [], start=1):
                    if (
                        len(lt.get("text", "")) != 1
                        or lt.get("confidence", 0.0) <= 0.0
                        or not _valid_geometry(lt)
                    ):
                        continue
                    letters.append(
                        ReceiptLetter(
                            image_id=image_id,
                            receipt_id=receipt_idx,
                            line_id=line_idx,
                            word_id=word_idx,
                            letter_id=letter_idx,
                            text=lt["text"],
                            bounding_box=lt["bounding_box"],
                            top_left=lt["top_left"],
                            top_right=lt["top_right"],
                            bottom_left=lt["bottom_left"],
                            bottom_right=lt["bottom_right"],
                            angle_degrees=lt.get("angle_degrees", 0.0),
                            angle_radians=lt.get("angle_radians", 0.0),
                            confidence=lt["confidence"],
                        )
                    )
    return receipts, lines, words, letters


# --------------------------------------------------------------------------- #
# Apply (live)                                                                #
# --------------------------------------------------------------------------- #


def apply_image(
    client: Any,
    table_name: str,
    image_id: str,
    raw_dir: Path,
    binary: Path,
    dry_run: bool,
) -> dict[str, Any]:
    raw_path = raw_dir / f"{image_id}.png"
    if not raw_path.exists():
        return {"status": "no-raw"}

    # 1. Current partition state.
    items = reh._query_partition(client, table_name, f"IMAGE#{image_id}")
    from scripts import local_analytics_cache as cache

    old_words: dict[tuple[int, int, int], dict[str, Any]] = {}
    old_labels: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    old_receipt_sks: list[tuple[str, str]] = []  # stale candidates
    place_rows: list[dict[str, Any]] = []
    for it in items:
        native = cache._native_item(it)
        pk, sk = str(native.get("PK", "")), str(native.get("SK", ""))
        etype = native.get("TYPE")
        wkey = reh.parse_word_sk(sk)
        if wkey and etype == "RECEIPT_WORD":
            if "bounding_box" in native or ("top_left" in native and "bottom_right" in native):
                old_words[wkey] = {
                    "text": _norm(native.get("text")),
                    "c": _centroid(native),
                }
        lkey = reh.parse_label_sk(sk)
        if lkey:
            # The label string lives ONLY in the SK — label rows carry no
            # separate "label" attribute. Inject the parsed value so the
            # planner and the rebuilt entity always have it.
            native = dict(native)
            native["label"] = lkey[3]
            old_labels[(lkey[0], lkey[1], lkey[2])].append(native)
        if etype == "RECEIPT_PLACE":
            place_rows.append(native)
        if sk.startswith(STALE_SK_PREFIX) and etype not in PRESERVED_TYPES:
            old_receipt_sks.append((pk, sk))

    n_old_labels = sum(len(v) for v in old_labels.values())

    # 2. New OCR.
    ocr = run_swift_ocr(binary, raw_path)
    if not ocr or not (ocr.get("receipts") or []):
        return {"status": "no-ocr", "labels": n_old_labels}
    raw_bucket = next(
        (
            str(cache._native_item(it).get("raw_s3_bucket"))
            for it in items
            if cache._native_item(it).get("TYPE") == "IMAGE"
        ),
        "unknown",
    )
    timestamp = cache._utc_now()
    receipts, lines, words, letters = build_new_entities(
        image_id, ocr, raw_bucket, timestamp
    )
    new_words_map = {
        (w.receipt_id, w.line_id, w.word_id): {
            "text": _norm(w.text),
            "c": (
                (w.top_left["x"] + w.bottom_right["x"]) / 2.0,
                (w.top_left["y"] + w.bottom_right["y"]) / 2.0,
            ),
        }
        for w in words
    }

    # 3. Label plan.
    plan = plan_label_moves(old_words, dict(old_labels), new_words_map)
    label_entities = [moved_label_entity(image_id, m) for m in plan.moves] + [
        moved_label_entity(
            image_id,
            LabelMove(p.old_key, p.new_key, p.native, p.label),
            parked=True,
            old_word_text=old_words.get(p.old_key, {}).get("text", ""),
        )
        for p in plan.parks
    ]

    # 4. Writes: puts first (overwrite semantics), then stale deletes.
    new_items = (
        [r.to_item() for r in receipts]
        + [ln.to_item() for ln in lines]
        + [w.to_item() for w in words]
        + [lt.to_item() for lt in letters]
        + [lb.to_item() for lb in label_entities]
    )
    new_keys = {(i["PK"]["S"], i["SK"]["S"]) for i in new_items}
    if len(new_keys) != len(new_items):
        raise RuntimeError(
            f"duplicate keys in new items ({len(new_items) - len(new_keys)}); "
            "refusing to write"
        )
    pre_orphan_sks = {
        (
            f"IMAGE#{image_id}",
            f"RECEIPT#{k[0]:05d}#LINE#{k[1]:05d}#WORD#{k[2]:05d}#LABEL#{lab}",
        )
        for (k, lab) in plan.pre_orphaned
    }
    stale = [
        (pk, sk)
        for (pk, sk) in old_receipt_sks
        if (pk, sk) not in new_keys and (pk, sk) not in pre_orphan_sks
    ]

    result = {
        "status": "ok",
        "old_receipts": len({k[0] for k in old_words}),
        "new_receipts": len(receipts),
        "labels": n_old_labels,
        "moved": len(plan.moves),
        "parked": len(plan.parks),
        "pre_orphaned_untouched": len(plan.pre_orphaned),
        "puts": len(new_items),
        "stale_deletes": len(stale),
        "image_type": (ocr.get("classification") or {}).get("image_type"),
    }
    if dry_run:
        result["dry_run"] = True
        return result

    reh._batch_write(
        client,
        table_name,
        [{"PutRequest": {"Item": it}} for it in new_items],
    )
    reh._batch_write(
        client,
        table_name,
        [
            {"DeleteRequest": {"Key": {"PK": {"S": pk}, "SK": {"S": sk}}}}
            for pk, sk in stale
        ],
    )
    # 5. IMAGE row: new classification + receipt count.
    itype = (ocr.get("classification") or {}).get("image_type")
    if itype:
        client.update_item(
            TableName=table_name,
            Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": f"IMAGE#{image_id}"}},
            UpdateExpression="SET image_type = :t, receipt_count = :n",
            ExpressionAttributeValues={
                ":t": {"S": itype},
                ":n": {"N": str(len(receipts))},
            },
        )
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--endpoint-url", default="http://127.0.0.1:8000")
    p.add_argument("--allow-aws", action="store_true")
    p.add_argument("--table-name", required=True)
    p.add_argument("--region", default=None)
    p.add_argument("--images", type=Path, required=True)
    p.add_argument("--raw-dir", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True, help="receipt-ocr binary")
    p.add_argument(
        "--backup",
        type=Path,
        required=True,
        help="rollback backup covering every target image (refused otherwise)",
    )
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper())

    ids = reh._read_image_ids(args.images)
    if args.limit:
        ids = ids[: args.limit]

    # Precondition: the rollback backup must cover every target image.
    if not args.backup.exists():
        raise SystemExit(f"REFUSING: backup {args.backup} does not exist")
    backed_up = {
        r.image_id or reh.image_id_from_pk(r.pk) for r in reh.load_rows(args.backup)
    }
    missing = [i for i in ids if i not in backed_up]
    if missing:
        raise SystemExit(
            f"REFUSING: {len(missing)} target images missing from backup "
            f"(first: {missing[:3]})"
        )

    client = reh._make_client(args.endpoint_url, args.region, args.allow_aws)
    results: dict[str, Any] = {}
    for n, image_id in enumerate(ids, 1):
        try:
            results[image_id] = apply_image(
                client,
                args.table_name,
                image_id,
                args.raw_dir,
                args.binary,
                args.dry_run,
            )
        except Exception as exc:  # keep going; the report shows the failure
            LOG.exception("apply failed for %s", image_id)
            results[image_id] = {"status": "error", "error": str(exc)}
        LOG.info("[%d/%d] %s: %s", n, len(ids), image_id[:8], results[image_id])

    args.report.write_text(json.dumps(results, indent=2))
    ok = [r for r in results.values() if r["status"] == "ok"]
    errs = [i for i, r in results.items() if r["status"] in ("error", "no-ocr")]
    print(
        json.dumps(
            {
                "images": len(results),
                "ok": len(ok),
                "errors": errs,
                "labels": sum(r.get("labels", 0) for r in ok),
                "moved": sum(r.get("moved", 0) for r in ok),
                "parked": sum(r.get("parked", 0) for r in ok),
                "pre_orphaned_untouched": sum(
                    r.get("pre_orphaned_untouched", 0) for r in ok
                ),
            },
            indent=2,
        )
    )
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
