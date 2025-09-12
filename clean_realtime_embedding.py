#!/usr/bin/env python3
"""
Clean realtime embedding matcher:
 - Lists receipts missing ReceiptMetadata
 - For each, computes full address (preferring extracted_data.value) and canonical phone
 - Embeds address/phone lines (cache-aware)
 - Queries local Chroma lines snapshot (downloaded once if needed)
 - Counts evidence only when candidate receipt has exact same phone AND full address
 - Prints a dry-run suggestion per missing receipt when there is at least one exact phone match
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from receipt_dynamo.data._pulumi import load_env
from receipt_label.utils import get_client_manager
from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.vector_store import VectorClient
from receipt_label.embedding.line.realtime import embed_lines_realtime


@dataclass
class Env:
    chroma_bucket: Optional[str]
    lines_dir: Path
    cache_dir: Path


def _normalize_phone(text: str) -> str:
    digits = re.sub(r"\D+", "", str(text or ""))
    if digits.startswith("1") and len(digits) > 10:
        digits = digits[1:]
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


_SUFFIX_MAP = {
    "STREET": "ST",
    "ROAD": "RD",
    "AVENUE": "AVE",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "HIGHWAY": "HWY",
    "PARKWAY": "PKWY",
    "SUITE": "STE",
    "APARTMENT": "APT",
}


def _normalize_address(text: str) -> str:
    t = str(text or "").upper()
    t = re.sub(r"\s+", " ", t).strip(" ,.;:|/\\-\t")
    tokens = [tok.strip(",.;:|/\\-") for tok in t.split(" ") if tok]
    return " ".join(_SUFFIX_MAP.get(tok, tok) for tok in tokens)


def _build_full_address_from_words(words: List) -> str:
    vals: List[str] = []
    for w in words:
        try:
            if (
                getattr(w, "extracted_data", None)
                and w.extracted_data.get("type") == "address"
            ):
                val = str(w.extracted_data.get("value") or "").strip()
                if val:
                    vals.append(val)
        except Exception:
            pass
    if vals:
        vals = sorted(
            {v.strip() for v in vals if v.strip()}, key=len, reverse=True
        )
        return _normalize_address(vals[0])
    return ""


def _build_full_address_from_lines(lines: List) -> str:
    if not lines:
        return ""
    parts = [
        str(ln.text or "")
        for ln in sorted(lines, key=lambda x: int(x.line_id))
        if not getattr(ln, "is_noise", False)
    ]
    return _normalize_address(" ".join(parts))


def _classify_line_type(line_text: str, preferred: Set[str]) -> str:
    if "phone" in preferred:
        return "phone"
    if "address" in preferred:
        return "address"
    if re.search(r"\d{3}[^\d]*\d{3}[^\d]*\d{4}", line_text or ""):
        return "phone"
    if re.search(
        r"\b(ave|avenue|blvd|boulevard|st|street|rd|road|dr|drive|ln|lane|way|hwy|highway|pkwy|suite|ste|apt|unit)\b",
        str(line_text).lower(),
    ):
        return "address"
    return "other"


def _ensure_lines_snapshot(env: Env) -> None:
    if any(env.lines_dir.rglob("*")):
        print(f"Using existing local lines snapshot at: {str(env.lines_dir)}")
        return
    if not env.chroma_bucket:
        raise RuntimeError(
            "No chromadb_bucket_name configured and no local lines snapshot found"
        )
    print(
        f"Downloading lines snapshot from s3://{env.chroma_bucket}/lines/snapshot/... to {str(env.lines_dir)}"
    )
    download_snapshot_atomic(
        bucket=env.chroma_bucket,
        collection="lines",
        local_path=str(env.lines_dir),
    )


def _get_target_phone(words: List) -> str:
    for w in words:
        try:
            if (
                getattr(w, "extracted_data", None)
                and w.extracted_data.get("type") == "phone"
            ):
                v = w.extracted_data.get("value") or w.text
                ph = _normalize_phone(v)
                if len(ph) >= 10:
                    return ph
        except Exception:
            pass
    return ""


def _get_cand_phone(
    cm, image_id: str, receipt_id: int, cache: Dict[Tuple[str, int], str]
) -> str:
    key = (str(image_id), int(receipt_id))
    if key in cache:
        return cache[key]
    try:
        cwords = cm.dynamo.list_receipt_words_from_receipt(
            image_id=str(image_id), receipt_id=int(receipt_id)
        )
        for w in cwords:
            if (
                getattr(w, "extracted_data", None)
                and w.extracted_data.get("type") == "phone"
            ):
                v = w.extracted_data.get("value") or w.text
                ph = _normalize_phone(v)
                if len(ph) >= 10:
                    cache[key] = ph
                    return ph
    except Exception:
        pass
    cache[key] = ""
    return ""


def _get_cand_address(
    cm, image_id: str, receipt_id: int, cache: Dict[Tuple[str, int], str]
) -> str:
    key = (str(image_id), int(receipt_id))
    if key in cache:
        return cache[key]
    try:
        cwords = cm.dynamo.list_receipt_words_from_receipt(
            image_id=str(image_id), receipt_id=int(receipt_id)
        )
        addr = _build_full_address_from_words(cwords)
        if not addr:
            clines = cm.dynamo.list_receipt_lines_from_receipt(
                image_id=str(image_id), receipt_id=int(receipt_id)
            )
            addr = _build_full_address_from_lines(clines)
        cache[key] = addr
        return addr
    except Exception:
        cache[key] = ""
        return ""


def process_missing_receipt(
    cm,
    line_client,
    env: Env,
    image_id: str,
    receipt_id: int,
) -> None:
    print(
        f"\nProcessing missing receipt image_id={image_id} receipt_id={receipt_id}"
    )

    # Fetch words
    try:
        words = cm.dynamo.list_receipt_words_from_receipt(
            image_id=str(image_id), receipt_id=int(receipt_id)
        )
    except Exception as e:
        print(f"Failed to fetch words: {e}")
        return
    if not words:
        print("No words found on the target receipt")
        return

    # Build phone/address for target
    target_full_address = _build_full_address_from_words(words)
    if not target_full_address:
        try:
            all_lines_tmp = cm.dynamo.list_receipt_lines_from_receipt(
                image_id=str(image_id), receipt_id=int(receipt_id)
            )
            target_full_address = _build_full_address_from_lines(all_lines_tmp)
        except Exception:
            target_full_address = ""
    target_phone = _get_target_phone(words)

    # Determine which lines to embed (only those with extracted address/phone)
    words_with_extracted_data = [
        w
        for w in words
        if getattr(w, "extracted_data", None)
        and w.extracted_data.get("type") in ("address", "phone")
    ]
    print(
        f"Words with extracted_data: {len(words_with_extracted_data)}/{len(words)}"
    )
    if not words_with_extracted_data:
        print("No address/phone words; skipping")
        return

    line_types_by_id: Dict[int, Set[str]] = {}
    for w in words_with_extracted_data:
        lid = int(w.line_id)
        line_types_by_id.setdefault(lid, set()).add(
            str(w.extracted_data.get("type", ""))
        )

    target_line_ids = sorted(
        {int(w.line_id) for w in words_with_extracted_data}
    )
    try:
        lines = cm.dynamo.get_receipt_lines_by_indices(
            [
                (str(image_id), int(receipt_id), int(lid))
                for lid in target_line_ids
            ]
        )
    except Exception as e:
        print(f"Primary line fetch failed, falling back: {e}")
        try:
            all_lines = cm.dynamo.list_receipt_lines_from_receipt(
                image_id=str(image_id), receipt_id=int(receipt_id)
            )
            lines = [
                ln for ln in all_lines if int(ln.line_id) in target_line_ids
            ]
        except Exception as e2:
            print(f"Failed to load receipt lines: {e2}")
            return
    if not lines:
        print("No lines found for target ids")
        return

    # Cache-aware embeddings
    cache_dir = env.cache_dir / "line_embeddings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{image_id}_{int(receipt_id)}.json"
    cached: Dict[int, List[float]] = {}
    if cache_file.exists():
        try:
            with cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                for v in data.get("vectors", []):
                    cached[int(v["line_id"])] = v["embedding"]
            print(
                f"Loaded cached line embeddings: {len(cached)} from {str(cache_file)}"
            )
        except Exception as e:
            print(f"Failed to read line cache (will re-embed as needed): {e}")

    to_embed = [
        ln
        for ln in lines
        if not getattr(ln, "is_noise", False) and int(ln.line_id) not in cached
    ]
    new_pairs = []
    if to_embed:
        try:
            new_pairs = embed_lines_realtime(to_embed, merchant_name=None)
        except Exception as e:
            print(f"Line embedding failed: {e}")
            return
    else:
        print(
            "All target lines already cached; skipping line embedding API call"
        )
    for ln, emb in new_pairs:
        cached[int(ln.line_id)] = emb
    if not cached:
        print("No line embeddings available")
        return

    try:
        payload = {
            "image_id": image_id,
            "receipt_id": int(receipt_id),
            "model": "text-embedding-3-small",
            "vectors": [
                {"line_id": lid, "embedding": emb}
                for lid, emb in cached.items()
            ],
        }
        with cache_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"Saved line embeddings cache to {str(cache_file)}")
    except Exception as e:
        print(f"Failed to write line cache: {e}")

    # Query and aggregate with exact phone/address per candidate receipt
    print("\nQuerying similar lines for target lines...")
    merchant_scores: Dict[str, float] = {}
    merchant_evidence: Dict[str, Dict[str, int]] = {}
    merchant_examples: Dict[str, List[str]] = {}
    merchant_best_phone: Dict[str, str] = {}
    merchant_best_address: Dict[str, str] = {}

    cand_phone_cache: Dict[Tuple[str, int], str] = {}
    cand_addr_cache: Dict[Tuple[str, int], str] = {}

    for ln in lines:
        lid = int(ln.line_id)
        if lid not in cached:
            continue
        try:
            res = line_client.query(
                collection_name="lines",
                query_embeddings=[cached[lid]],
                n_results=10,
                include=["metadatas", "documents", "distances"],
            )
        except Exception as e:
            print(f"Line query failed for line_id={lid}: {e}")
            continue

        print(f"\nLine '{str(ln.text)[:60]}' (line_id={lid})")
        if not (res and res.get("metadatas")):
            print("  no matches")
            continue

        metas = res["metadatas"][0]
        docs = res.get("documents", [["?"]])[0]
        dists = res.get("distances", [[None]])[0]
        for i in range(min(5, len(metas))):
            md = metas[i] or {}
            print(
                f"  match[{i}]: text='{docs[i]}' receipt_id={md.get('receipt_id')} image_id={md.get('image_id')} merchant={md.get('merchant_name')} dist={dists[i]}"
            )

        preferred = set(str(t) for t in line_types_by_id.get(lid, set()))
        q_type = _classify_line_type(str(ln.text), preferred)
        if q_type not in ("phone", "address"):
            continue

        for i in range(len(metas)):
            md = metas[i] or {}
            doc_text = docs[i] if i < len(docs) else ""
            dist_val = dists[i] if i < len(dists) else None
            if dist_val is None:
                continue
            sd = 1.0 / (1.0 + float(dist_val))

            merchant = md.get("merchant_name") or "UNKNOWN"
            base_weight = 3.0 if q_type == "phone" else 1.0
            score = base_weight * sd

            img_id = md.get("image_id")
            rec_id = md.get("receipt_id")
            if q_type == "phone":
                cand_phone = (
                    _get_cand_phone(
                        cm, str(img_id), int(rec_id), cand_phone_cache
                    )
                    if img_id and rec_id is not None
                    else ""
                )
                if target_phone and cand_phone and cand_phone == target_phone:
                    score += 3.0
                    merchant_best_phone.setdefault(merchant, target_phone)
                else:
                    continue
            else:
                cand_addr_full = (
                    _get_cand_address(
                        cm, str(img_id), int(rec_id), cand_addr_cache
                    )
                    if img_id and rec_id is not None
                    else ""
                )
                if (
                    target_full_address
                    and cand_addr_full
                    and cand_addr_full == target_full_address
                ):
                    score += 2.0
                    merchant_best_address.setdefault(merchant, cand_addr_full)
                else:
                    continue

            merchant_scores[merchant] = (
                merchant_scores.get(merchant, 0.0) + score
            )
            ev = merchant_evidence.setdefault(
                merchant, {"phone": 0, "address": 0}
            )
            ev[q_type] += 1
            merchant_examples.setdefault(merchant, []).append(
                f"{q_type}: '{str(ln.text)[:32]}' ↔ '{str(doc_text)[:32]}' (d={dist_val:.4g})"
            )

    if merchant_scores:
        print(
            "\nAggregated merchant ranking (combined phone/address evidence):"
        )
        ranked = sorted(
            merchant_scores.items(), key=lambda kv: kv[1], reverse=True
        )
        for rank, (m, total) in enumerate(ranked[:10], start=1):
            ev = merchant_evidence.get(m, {})
            ex = merchant_examples.get(m, [])
            sample = ex[0] if ex else ""
            print(
                f"  {rank}. merchant={m} score={total:.3f} evidence={{phone:{ev.get('phone',0)}, address:{ev.get('address',0)}}} example={sample}"
            )

        top_merchant, top_score = ranked[0]
        ev_top = merchant_evidence.get(
            top_merchant, {"phone": 0, "address": 0}
        )
        phone_digits = merchant_best_phone.get(top_merchant, target_phone)
        addr_text = merchant_best_address.get(
            top_merchant, target_full_address
        )

        if ev_top.get("phone", 0) >= 1 and phone_digits:
            suggestion = {
                "image_id": str(image_id),
                "receipt_id": int(receipt_id),
                "place_id": "",
                "merchant_name": top_merchant,
                "merchant_category": "",
                "address": addr_text,
                "phone_number": phone_digits,
                "matched_fields": [
                    f
                    for f, c in [
                        ("phone", ev_top.get("phone", 0)),
                        ("address", ev_top.get("address", 0)),
                    ]
                    if c > 0
                ],
                "validated_by": "VECTOR_MATCH",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reasoning": (
                    f"Chosen via Chroma identity resolution. Top merchant='{top_merchant}' "
                    f"score={top_score:.3f} evidence=phone:{ev_top.get('phone',0)} address:{ev_top.get('address',0)}"
                ),
            }
            print("\nSuggested ReceiptMetadata correction (dry-run):")
            print(json.dumps(suggestion, indent=2))
        else:
            print(
                "\nNo safe auto-correction suggested (requires at least one exact phone match)."
            )


def main() -> None:
    # env and clients
    pulumi_env = load_env("dev")
    env = Env(
        chroma_bucket=pulumi_env.get("chromadb_bucket_name"),
        lines_dir=(Path(__file__).parent / "dev.chroma_lines").resolve(),
        cache_dir=(Path(__file__).parent / "dev.cache").resolve(),
    )

    # Ensure required env vars for client manager
    dyn_table = pulumi_env.get("dynamodb_table_name") or pulumi_env.get(
        "DYNAMODB_TABLE_NAME"
    )
    if dyn_table and not os.environ.get("DYNAMODB_TABLE_NAME"):
        os.environ["DYNAMODB_TABLE_NAME"] = str(dyn_table)

    cm = get_client_manager()

    # list receipts and find missing
    receipts_all = []
    receipts, lek = cm.dynamo.list_receipts()
    receipts_all.extend(receipts)
    while lek:
        nxt, lek = cm.dynamo.list_receipts(last_evaluated_key=lek)
        receipts_all.extend(nxt)

    indices = [(r.image_id, r.receipt_id) for r in receipts_all]
    metas = cm.dynamo.get_receipt_metadatas_by_indices(indices)
    have = {(m.image_id, m.receipt_id) for m in metas}
    missing = [idx for idx in indices if idx not in have]
    print(
        f"Total receipts: {len(indices)} | With metadata: {len(have)} | Missing: {len(missing)}"
    )
    if not missing:
        return

    _ensure_lines_snapshot(env)
    line_client = VectorClient.create_line_client(
        persist_directory=str(env.lines_dir), mode="read"
    )

    for image_id, receipt_id in missing:
        process_missing_receipt(
            cm, line_client, env, str(image_id), int(receipt_id)
        )


if __name__ == "__main__":
    main()
