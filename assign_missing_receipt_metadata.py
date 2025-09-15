#!/usr/bin/env python3
import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.constants import ValidationMethod, MerchantValidationStatus

# Shared normalization utilities
from receipt_label.merchant_validation.normalize import (
    build_full_address_from_lines,
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
    normalize_address,
)
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.vector_store import VectorClient
from receipt_label.utils import get_client_manager
from openai import OpenAI


# -----------------------------------------------------------------------------
# Inputs and environment
# -----------------------------------------------------------------------------
ROOT = Path(__file__).parent
# Ensure local package import works when not installed in editable mode
sys.path.insert(0, str(ROOT / "receipt_label"))
WORDS_EXPORT = ROOT / "dev.words.ndjson"
LINES_EXPORT = ROOT / "dev.lines.ndjson"

env = load_env()
DDB_TABLE = env.get("dynamodb_table_name")


# -----------------------------------------------------------------------------
# IO helpers
# -----------------------------------------------------------------------------


def _load_words_lines(
    client: DynamoClient,
) -> Tuple[List[ReceiptWord], List[ReceiptLine]]:
    if not WORDS_EXPORT.exists() or not LINES_EXPORT.exists():
        words, _ = client.list_receipt_words()
        lines, _ = client.list_receipt_lines()
        if WORDS_EXPORT.exists():
            WORDS_EXPORT.unlink()
        with open(WORDS_EXPORT, "w", encoding="utf-8") as wf:
            for w in words:
                wf.write(json.dumps(dict(w)) + "\n")
        if LINES_EXPORT.exists():
            LINES_EXPORT.unlink()
        with open(LINES_EXPORT, "w", encoding="utf-8") as lf:
            for ln in lines:
                lf.write(json.dumps(dict(ln)) + "\n")
        return words, lines

    words: List[ReceiptWord] = []
    lines: List[ReceiptLine] = []
    with open(WORDS_EXPORT, "r", encoding="utf-8") as wf:
        for line in wf:
            words.append(ReceiptWord(**json.loads(line)))
    with open(LINES_EXPORT, "r", encoding="utf-8") as lf:
        for line in lf:
            lines.append(ReceiptLine(**json.loads(line)))
    return words, lines


# -----------------------------------------------------------------------------
# Clustering by normalized anchors
# -----------------------------------------------------------------------------
ReceiptKey = Tuple[str, int]


def _addresses_by_receipt(
    words: List[ReceiptWord],
    lines: List[ReceiptLine],
) -> Dict[ReceiptKey, str]:
    # Prefer address values from anchor words if present; otherwise build from lines
    words_by_receipt: Dict[ReceiptKey, List[ReceiptWord]] = defaultdict(list)
    lines_by_receipt: Dict[ReceiptKey, List[ReceiptLine]] = defaultdict(list)

    for w in words:
        rk = (str(w.image_id), int(w.receipt_id))
        words_by_receipt[rk].append(w)
    for ln in lines:
        rk = (str(ln.image_id), int(ln.receipt_id))
        lines_by_receipt[rk].append(ln)

    results: Dict[ReceiptKey, str] = {}
    for rk, wlist in words_by_receipt.items():
        addr = build_full_address_from_words(wlist)
        if not addr:
            llist = sorted(
                lines_by_receipt.get(rk, []), key=lambda l: int(l.line_id)
            )
            addr = build_full_address_from_lines(llist)
        results[rk] = addr
    # Include any receipts that only have lines present
    for rk, llist in lines_by_receipt.items():
        if rk not in results:
            results[rk] = build_full_address_from_lines(
                sorted(llist, key=lambda l: int(l.line_id))
            )
    return results


def _phones_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "phone":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        digits = normalize_phone(
            str(w.extracted_data.get("value") or w.text or "")
        )
        if digits:
            results[rk].add(digits)
    return results


def _urls_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "url":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        u = normalize_url(str(w.extracted_data.get("value") or w.text or ""))
        if u:
            results[rk].add(u)
    return results


# -----------------------------------------------------------------------------
# Metadata selection within clusters
# -----------------------------------------------------------------------------


def _fetch_existing_metadata_map(
    client: DynamoClient, indices: Iterable[ReceiptKey]
) -> Dict[ReceiptKey, ReceiptMetadata]:
    index_list = list({(img, rec) for img, rec in indices})
    if not index_list:
        return {}
    # Batch to avoid per-receipt fetch cost
    metadatas = client.get_receipt_metadatas_by_indices(index_list)
    return {(m.image_id, int(m.receipt_id)): m for m in metadatas}


def _choose_canonical_metadata(
    metas: List[ReceiptMetadata],
) -> Optional[ReceiptMetadata]:
    if not metas:
        return None

    # Heuristic: prefer MATCHED, then canonical fields, then matched_fields size
    def score(m: ReceiptMetadata) -> Tuple[int, int, int, str]:
        is_matched = int(
            (m.validation_status or "").upper()
            == MerchantValidationStatus.MATCHED.value
        )
        has_canonical = int(
            bool(
                m.canonical_place_id
                or m.canonical_address
                or m.canonical_phone_number
            )
        )
        matched_count = len(m.matched_fields or [])
        return (
            is_matched,
            has_canonical,
            matched_count,
            m.merchant_name or "",
        )

    return sorted(metas, key=score, reverse=True)[0]


def _build_proposed_from_canonical(
    canonical: ReceiptMetadata, target: ReceiptKey, matched_reasons: List[str]
) -> ReceiptMetadata:
    image_id, receipt_id = target
    return ReceiptMetadata(
        image_id=str(image_id),
        receipt_id=int(receipt_id),
        place_id=canonical.canonical_place_id or canonical.place_id or "",
        merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        matched_fields=list(
            set([*(canonical.matched_fields or []), *matched_reasons])
        ),
        timestamp=datetime.now(timezone.utc),
        merchant_category=canonical.merchant_category or "",
        address=canonical.canonical_address or canonical.address or "",
        phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=(
            "Derived from cluster neighbor with existing metadata; "
            + ", ".join(sorted(set(matched_reasons)))
        ),
        canonical_place_id=canonical.canonical_place_id
        or canonical.place_id
        or "",
        canonical_merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        canonical_address=canonical.canonical_address
        or canonical.address
        or "",
        canonical_phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validation_status="",
    )


# -----------------------------------------------------------------------------
# Main workflow
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find similar receipts by clustering normalized address/phone/url and "
            "propose ReceiptMetadata for receipts lacking metadata."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="If set, write proposed ReceiptMetadata records to DynamoDB (batch).",
    )
    parser.add_argument(
        "--max-write",
        type=int,
        default=0,
        help="Optional cap on number of proposed records to write (0 means no cap).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Focus on specific receipts only. Format: IMAGE_ID:RECEIPT_ID. Can be repeated.",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="With --only, include receipts that already have metadata for analysis/logs.",
    )
    parser.add_argument(
        "--similarity-report",
        action="store_true",
        help="Run street-first and phone-line similarity over receipts lacking metadata and print a summary.",
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=str((ROOT / "reports/similarity_report.json").resolve()),
        help="File path to write full similarity report JSON.",
    )
    args = parser.parse_args()

    if not DDB_TABLE:
        raise ValueError("No dynamodb_table_name configured in environment")

    client = DynamoClient(DDB_TABLE)

    words, lines = _load_words_lines(client)

    # Build quick indexes
    words_by_receipt: Dict[ReceiptKey, List[ReceiptWord]] = defaultdict(list)
    for w in words:
        words_by_receipt[(str(w.image_id), int(w.receipt_id))].append(w)
    line_text_by_key: Dict[Tuple[str, int, int], str] = {}
    for ln in lines:
        line_text_by_key[
            (str(ln.image_id), int(ln.receipt_id), int(ln.line_id))
        ] = str(ln.text or "")
    # also build lines_by_key for quick text reconstruction
    lines_by_key: Dict[ReceiptKey, List[ReceiptLine]] = defaultdict(list)
    for ln in lines:
        lines_by_key[(str(ln.image_id), int(ln.receipt_id))].append(ln)

    # Text reconstruction helpers available to all paths
    reconstructor = ReceiptTextReconstructor()

    def _formatted_text(key: ReceiptKey) -> str:
        ln_list = lines_by_key.get(key, [])
        text, groups = reconstructor.reconstruct_receipt(ln_list)
        # If focusing, log header anchors from top groups
        if focus_keys and key in focus_keys:
            header_groups = groups[:5]
            header_text = " \n".join(g.text for g in header_groups)
            header_phone = normalize_phone(header_text)
            header_addr = normalize_address(header_text)
            print(
                "[debug][header]",
                key,
                {"header_phone": header_phone, "header_address": header_addr},
            )
        return text

    # Chroma lines client (local snapshot)
    local_lines_path = str((ROOT / "dev.chroma_lines").resolve())
    try:
        chroma_line_client = VectorClient.create_line_client(
            persist_directory=local_lines_path, mode="read"
        )
        lines_collection = chroma_line_client.get_collection("lines")
    except Exception:
        lines_collection = None

    # OpenAI client for query-time embeddings to match batch dims (1536)
    try:
        _cm = None  # avoid dependency on client_manager config
        _openai = (
            OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            if os.environ.get("OPENAI_API_KEY")
            else None
        )
    except Exception:
        _openai = None

    def _street_candidates_from_extracted(rk: ReceiptKey) -> List[str]:
        cands: List[str] = []
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "address":
                # Use the full line text for the street candidate (street-first heuristic)
                key = (str(w.image_id), int(w.receipt_id), int(w.line_id))
                street_line = line_text_by_key.get(key, "")
                if street_line:
                    cands.append(street_line)
        # Dedup, preserve order
        seen: Set[str] = set()
        out: List[str] = []
        for t in cands:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _phone_from_extracted(rk: ReceiptKey) -> str:
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                v = ext.get("value") or getattr(w, "text", "")
                ph = normalize_phone(str(v))
                if ph:
                    return ph
        return ""

    def _urls_by_receipt(
        words: List[ReceiptWord],
    ) -> Dict[ReceiptKey, Set[str]]:
        results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
        for w in words:
            if not w.extracted_data or w.extracted_data.get("type") != "url":
                continue
            rk = (str(w.image_id), int(w.receipt_id))
            u = normalize_url(
                str(w.extracted_data.get("value") or w.text or "")
            )
            if u:
                results[rk].add(u)
        return results

    def _phone_line_candidates_from_extracted(rk: ReceiptKey) -> List[str]:
        cands: List[str] = []
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                key = (str(w.image_id), int(w.receipt_id), int(w.line_id))
                phone_line = line_text_by_key.get(key, "")
                if phone_line:
                    cands.append(phone_line)
        seen: Set[str] = set()
        out: List[str] = []
        for t in cands:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _similarity_match_phone_first(rk: ReceiptKey) -> Dict:
        result: Dict = {"receipt": rk, "matches": []}
        if not lines_collection:
            result["error"] = "no_lines_collection"
            return result
        if _openai is None:
            result["error"] = "no_openai_client"
            return result
        phone_lines = _phone_line_candidates_from_extracted(rk)
        # limit to top few phone lines
        phone_lines = phone_lines[:2]
        if not phone_lines:
            result["error"] = "no_phone_on_receipt"
            return result
        for p in phone_lines:
            try:
                resp = _openai.embeddings.create(
                    model="text-embedding-3-small", input=[p]
                )
                emb = resp.data[0].embedding
                q = chroma_line_client.query(
                    collection_name="lines",
                    query_embeddings=[emb],
                    n_results=8,
                    include=["metadatas", "distances"],
                )
            except Exception as e:
                result.setdefault("errors", []).append(
                    {"phone_line": p, "error": str(e)}
                )
                continue
            mds = (q or {}).get("metadatas") or []
            dists = (q or {}).get("distances") or []
            phone_norm_line = normalize_phone(p)
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                try:
                    n_phone = str(
                        (md or {}).get("normalized_phone_10") or ""
                    ).strip()
                    n_addr = str(
                        (md or {}).get("normalized_full_address") or ""
                    ).strip()
                    n_img = str((md or {}).get("image_id") or "")
                    n_rec_str = (md or {}).get("receipt_id")
                    try:
                        n_rec = (
                            int(n_rec_str) if n_rec_str is not None else None
                        )
                    except Exception:
                        n_rec = None
                    phone_ok = bool(n_phone) and (phone_norm_line == n_phone)
                    why = {
                        "phone_line": p,
                        "phone_from_line": phone_norm_line,
                        "neighbor_phone": n_phone,
                        "neighbor_addr": n_addr,
                        "distance": dist,
                        "accepted": phone_ok,
                        "neighbor_receipt": [n_img, n_rec],
                    }
                    result["matches"].append(why)
                except Exception as e:
                    result.setdefault("errors", []).append(
                        {"md_error": str(e)}
                    )
        return result

    def _similarity_match_street_first(rk: ReceiptKey) -> Dict:
        result: Dict = {"receipt": rk, "matches": []}
        if not lines_collection:
            result["error"] = "no_lines_collection"
            return result
        street_lines = _street_candidates_from_extracted(rk)
        # limit to top few street candidates to control cost
        street_lines = street_lines[:3]
        phone_norm = _phone_from_extracted(rk)
        for s in street_lines:
            try:
                # Embed street text to match collection dims
                if _openai is None:
                    raise RuntimeError("no_openai_client")
                resp = _openai.embeddings.create(
                    model="text-embedding-3-small", input=[s]
                )
                emb = resp.data[0].embedding
                q = chroma_line_client.query(
                    collection_name="lines",
                    query_embeddings=[emb],
                    n_results=8,
                    include=["metadatas", "distances"],
                )
            except Exception as e:
                result.setdefault("errors", []).append(
                    {"street": s, "error": str(e)}
                )
                continue
            mds = (q or {}).get("metadatas") or []
            dists = (q or {}).get("distances") or []
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                try:
                    n_addr = str(
                        (md or {}).get("normalized_full_address") or ""
                    ).strip()
                    n_phone_inline = str(
                        (md or {}).get("normalized_phone_10") or ""
                    ).strip()
                    street_norm = normalize_address(s)
                    addr_ok = bool(n_addr) and (
                        street_norm and street_norm.split()[0] in n_addr
                    )

                    # Chroma-side receipt-anchor fallback via local words index
                    neighbor_img = str((md or {}).get("image_id") or "")
                    neighbor_rec_str = (md or {}).get("receipt_id")
                    try:
                        neighbor_rec = (
                            int(neighbor_rec_str)
                            if neighbor_rec_str is not None
                            else None
                        )
                    except Exception:
                        neighbor_rec = None
                    fallback_phone = ""
                    if neighbor_img and neighbor_rec is not None:
                        for w in words_by_receipt.get(
                            (neighbor_img, neighbor_rec), []
                        ):
                            ext = getattr(w, "extracted_data", None) or {}
                            if ext.get("type") == "phone":
                                v = ext.get("value") or getattr(w, "text", "")
                                fp = normalize_phone(str(v))
                                if fp:
                                    fallback_phone = fp
                                    break

                    # Phone gating: only enforce equality when both sides have phones
                    if not phone_norm:
                        phone_ok = True
                    elif n_phone_inline:
                        phone_ok = phone_norm == n_phone_inline
                    elif fallback_phone:
                        phone_ok = phone_norm == fallback_phone
                    else:
                        phone_ok = (
                            True  # neighbor has no phone anchors; don't block
                        )

                    accepted = bool(addr_ok and phone_ok)
                    why = {
                        "street": s,
                        "street_norm": street_norm,
                        "neighbor_addr": n_addr,
                        "neighbor_phone_inline": n_phone_inline,
                        "neighbor_phone_fallback": fallback_phone,
                        "receipt_phone": phone_norm,
                        "addr_ok": addr_ok,
                        "phone_ok": phone_ok,
                        "distance": dist,
                        "accepted": accepted,
                        "neighbor_receipt": [neighbor_img, neighbor_rec],
                    }
                    result["matches"].append(why)
                except Exception as e:
                    result.setdefault("errors", []).append(
                        {"md_error": str(e)}
                    )
        return result

    # Compute normalized anchors
    addr_by_receipt = _addresses_by_receipt(words, lines)
    phones_by_receipt = _phones_by_receipt(words)
    urls_by_receipt = _urls_by_receipt(words)

    # Build clusters keyed by address and url
    address_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, addr in addr_by_receipt.items():
        if addr and len(addr.split()) >= 3:
            address_clusters[addr].add(rk)

    url_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, urls in urls_by_receipt.items():
        for u in urls:
            if u:
                url_clusters[u].add(rk)

    # Identify receipts missing metadata
    all_receipts: Set[ReceiptKey] = set(addr_by_receipt.keys())

    # Apply focus filter if provided
    focus_keys: Set[ReceiptKey] = set()
    for only in args.only:
        try:
            img, rec = only.split(":", 1)
            focus_keys.add((img, int(rec)))
        except Exception:
            print(
                f"[warn] Invalid --only value, expected IMAGE_ID:RECEIPT_ID, got: {only}"
            )
    if focus_keys:
        all_receipts = {rk for rk in all_receipts if rk in focus_keys}
        if not all_receipts:
            print("[]")
            return
    existing_map = _fetch_existing_metadata_map(client, all_receipts)

    # If focusing and include-existing, augment all_receipts to ensure we analyze even those with metadata
    if focus_keys and args.include_existing:
        all_receipts = focus_keys

    proposed: List[ReceiptMetadata] = []
    selection_log: List[Dict] = []

    if not args.similarity_report:
        # Address-based propagation
        for addr, rks in address_clusters.items():
            # Skip clusters that don't intersect focus when focus is set
            if focus_keys and not (rks & focus_keys):
                continue
            cluster_list = sorted(list(rks))
            have = [
                existing_map.get(rk)
                for rk in cluster_list
                if rk in existing_map
            ]
            have = [m for m in have if m is not None]
            # Determine missing set; if include-existing and focusing, treat focus receipts as "missing" for analysis output
            if focus_keys and args.include_existing:
                missing = [rk for rk in (rks & focus_keys)]
            else:
                missing = [rk for rk in cluster_list if rk not in existing_map]
            if not have or not missing:
                continue
            canonical = _choose_canonical_metadata(have)
            if not canonical:
                continue
            # Debug: log cluster decision
            if focus_keys:
                print("[debug][address-cluster] key=", addr)
                print("[debug] receipts=", cluster_list)
                print(
                    "[debug] have_merchants=",
                    [
                        (
                            m.image_id,
                            int(m.receipt_id),
                            m.merchant_name,
                            m.address,
                            m.phone_number,
                        )
                        for m in have
                    ],
                )
                print(
                    "[debug] picked_canonical=",
                    (
                        canonical.image_id,
                        int(canonical.receipt_id),
                        canonical.merchant_name,
                    ),
                )
            for rk in missing:
                # Header-derived anchors for comparison
                header_text = _formatted_text(rk)
                header_phone = normalize_phone(header_text)
                header_addr = normalize_address(header_text)

                # Basic city/zip token overlap diagnostics
                def _zip_of(addr_str: str) -> str:
                    import re as _re

                    m = _re.search(r"\b(\d{5})(?:-\d{4})?\b", addr_str)
                    return m.group(1) if m else ""

                def _city_of(addr_str: str) -> str:
                    # crude: take token before state if present
                    tokens = addr_str.split()
                    try:
                        idx = tokens.index("CA")
                        return " ".join(tokens[max(0, idx - 2) : idx])
                    except ValueError:
                        return ""

                cz_header = (_city_of(header_addr), _zip_of(header_addr))
                cz_canon = (
                    _city_of(
                        normalize_address(
                            canonical.address
                            or canonical.canonical_address
                            or ""
                        )
                    ),
                    _zip_of(
                        canonical.address or canonical.canonical_address or ""
                    ),
                )
                phones_match = bool(header_phone) and (
                    header_phone
                    == normalize_phone(
                        canonical.phone_number
                        or canonical.canonical_phone_number
                        or ""
                    )
                )
                print(
                    "[debug][compare]",
                    rk,
                    {
                        "header_phone": header_phone,
                        "canonical_phone": canonical.phone_number
                        or canonical.canonical_phone_number,
                        "phones_match": phones_match,
                        "header_address": header_addr,
                        "canonical_address": canonical.address
                        or canonical.canonical_address,
                        "city_zip_header": cz_header,
                        "city_zip_canonical": cz_canon,
                    },
                )
                reasons = ["address"]
                if phones_match:
                    reasons.append("phone")
                proposed.append(
                    _build_proposed_from_canonical(canonical, rk, reasons)
                )
            selection_log.append(
                {
                    "cluster_type": "address",
                    "key": addr,
                    "receipts": cluster_list,
                    "canonical_source": {
                        "image_id": canonical.image_id,
                        "receipt_id": int(canonical.receipt_id),
                        "merchant_name": canonical.merchant_name,
                    },
                    "proposed_count": len(missing),
                }
            )

        # URL-based propagation (only for those still missing)
        existing_after_addr = _fetch_existing_metadata_map(
            client, [rk for rk in all_receipts]
        )
        missing_now = [
            rk
            for rk in all_receipts
            if rk not in existing_after_addr
            and rk not in {(p.image_id, int(p.receipt_id)) for p in proposed}
        ]

        for url, rks in url_clusters.items():
            if focus_keys and not (rks & focus_keys):
                continue
            cluster_list = sorted(list(rks))
            have = [
                existing_after_addr.get(rk)
                for rk in cluster_list
                if rk in existing_after_addr
            ]
            have = [m for m in have if m is not None]
            if focus_keys and args.include_existing:
                missing = [rk for rk in (rks & focus_keys)]
            else:
                # exclude those already proposed
                missing = [rk for rk in cluster_list if rk in missing_now]
            if not have or not missing:
                continue
            canonical = _choose_canonical_metadata(have)
            if not canonical:
                continue
            if focus_keys:
                print("[debug][url-cluster] key=", url)
                print("[debug] receipts=", cluster_list)
                print(
                    "[debug] have_merchants=",
                    [
                        (
                            m.image_id,
                            int(m.receipt_id),
                            m.merchant_name,
                            m.address,
                            m.phone_number,
                        )
                        for m in have
                    ],
                )
                print(
                    "[debug] picked_canonical=",
                    (
                        canonical.image_id,
                        int(canonical.receipt_id),
                        canonical.merchant_name,
                    ),
                )
            for rk in missing:
                header_text = _formatted_text(rk)
                header_phone = normalize_phone(header_text)
                print(
                    "[debug][compare-url]",
                    rk,
                    {
                        "header_phone": header_phone,
                        "canonical_phone": canonical.phone_number
                        or canonical.canonical_phone_number,
                    },
                )
                proposed.append(
                    _build_proposed_from_canonical(canonical, rk, ["url"])
                )
            selection_log.append(
                {
                    "cluster_type": "url",
                    "key": url,
                    "receipts": cluster_list,
                    "canonical_source": {
                        "image_id": canonical.image_id,
                        "receipt_id": int(canonical.receipt_id),
                        "merchant_name": canonical.merchant_name,
                    },
                    "proposed_count": len(missing),
                }
            )

    # Deduplicate proposed by (image_id, receipt_id)
    deduped: Dict[ReceiptKey, ReceiptMetadata] = {}
    for p in proposed:
        key = (p.image_id, int(p.receipt_id))
        if key not in deduped:
            deduped[key] = p
        else:
            existing = deduped[key]
            # merge matched_fields and reasoning
            existing.matched_fields = sorted(
                list(
                    set(
                        (existing.matched_fields or [])
                        + (p.matched_fields or [])
                    )
                )
            )
            if p.reasoning and p.reasoning not in (existing.reasoning or ""):
                existing.reasoning = (
                    (existing.reasoning + "; " + p.reasoning)
                    if existing.reasoning
                    else p.reasoning
                )

    # Serialize entities to plain JSON objects
    def _serialize_entity(m: ReceiptMetadata) -> Dict:
        return {
            "image_id": m.image_id,
            "receipt_id": int(m.receipt_id),
            "place_id": m.place_id,
            "merchant_name": m.merchant_name,
            "merchant_category": m.merchant_category,
            "address": m.address,
            "phone_number": m.phone_number,
            "matched_fields": m.matched_fields,
            "validated_by": m.validated_by,
            "timestamp": m.timestamp.isoformat() if m.timestamp else "",
            "reasoning": m.reasoning,
            "canonical_place_id": m.canonical_place_id,
            "canonical_merchant_name": m.canonical_merchant_name,
            "canonical_address": m.canonical_address,
            "canonical_phone_number": m.canonical_phone_number,
            "validation_status": m.validation_status,
        }

    proposed_items = [_serialize_entity(v) for v in deduped.values()]

    output: List[Dict] = []
    for key, m in deduped.items():
        output.append(
            {
                "receipt_metadata": _serialize_entity(m),
                "formatted_text": _formatted_text(key),
            }
        )

    # Apply focus filter to output
    if focus_keys:
        # If include-existing, also run similarity and attach diagnostics
        if args.include_existing:
            sim_logs = []
            phone_sim_logs = []
            for rk in focus_keys:
                sim_logs.append(_similarity_match_street_first(rk))
                phone_sim_logs.append(_similarity_match_phone_first(rk))
            print(
                json.dumps(
                    {
                        "similarity": sim_logs,
                        "phone_similarity": phone_sim_logs,
                        "results": output,
                    },
                    indent=2,
                )
            )
            return
        output = [
            o
            for o in output
            if (
                o["receipt_metadata"]["image_id"],
                int(o["receipt_metadata"]["receipt_id"]),
            )
            in focus_keys
        ]

    # Optional similarity report across receipts lacking metadata
    if args.similarity_report:
        # Build set of receipts missing metadata
        existing_all = _fetch_existing_metadata_map(
            client, addr_by_receipt.keys()
        )
        missing_receipts = [
            rk for rk in addr_by_receipt.keys() if rk not in existing_all
        ]
        matched = []
        unmatched = []
        reconstructor = ReceiptTextReconstructor()

        # helper to get full text for a receipt
        def _receipt_text(rk: ReceiptKey) -> str:
            ln_list = lines_by_key.get(rk, [])
            text, _ = reconstructor.reconstruct_receipt(ln_list)
            return text or ""

        for rk in missing_receipts:
            street = _similarity_match_street_first(rk)
            phone = _similarity_match_phone_first(rk)
            accepted_street = [
                m for m in street.get("matches", []) if m.get("accepted")
            ]
            accepted_phone = [
                m for m in phone.get("matches", []) if m.get("accepted")
            ]
            if accepted_street or accepted_phone:
                reasons = []
                text = _receipt_text(rk).lower()
                # gather reasons and merchant checks
                for m in accepted_street:
                    neighbor_key = tuple(
                        m.get("neighbor_receipt") or ["", None]
                    )
                    merchant_name = ""
                    if (
                        neighbor_key
                        and neighbor_key[0]
                        and neighbor_key[1] is not None
                    ):
                        nm = existing_all.get(
                            (neighbor_key[0], int(neighbor_key[1]))
                        )
                        if nm:
                            merchant_name = (
                                nm.canonical_merchant_name
                                or nm.merchant_name
                                or ""
                            )
                    reasons.append(
                        {
                            "via": "street",
                            "street": m.get("street"),
                            "neighbor_receipt": m.get("neighbor_receipt"),
                            "addr_ok": m.get("addr_ok"),
                            "phone_ok": m.get("phone_ok"),
                            "distance": m.get("distance"),
                            "merchant_name": merchant_name,
                            "merchant_in_text": bool(
                                merchant_name and merchant_name.lower() in text
                            ),
                        }
                    )
                for m in accepted_phone:
                    neighbor_key = tuple(
                        m.get("neighbor_receipt") or ["", None]
                    )
                    merchant_name = ""
                    if (
                        neighbor_key
                        and neighbor_key[0]
                        and neighbor_key[1] is not None
                    ):
                        nm = existing_all.get(
                            (neighbor_key[0], int(neighbor_key[1]))
                        )
                        if nm:
                            merchant_name = (
                                nm.canonical_merchant_name
                                or nm.merchant_name
                                or ""
                            )
                    reasons.append(
                        {
                            "via": "phone",
                            "phone_line": m.get("phone_line"),
                            "neighbor_phone": m.get("neighbor_phone"),
                            "neighbor_receipt": m.get("neighbor_receipt"),
                            "distance": m.get("distance"),
                            "merchant_name": merchant_name,
                            "merchant_in_text": bool(
                                merchant_name and merchant_name.lower() in text
                            ),
                        }
                    )
                matched.append({"receipt": rk, "reasons": reasons})
            else:
                why = {
                    "street_errors": street.get("errors"),
                    "phone_errors": phone.get("errors"),
                    "street_candidates": len(street.get("matches", [])),
                    "phone_candidates": len(phone.get("matches", [])),
                }
                unmatched.append({"receipt": rk, "why": why})
        report = {
            "total_missing": len(missing_receipts),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "matched": matched,
            "unmatched": unmatched,
        }
        # ensure directory exists
        rp = Path(args.report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        with rp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "report_path": str(rp),
                    "total_missing": report["total_missing"],
                    "matched_count": report["matched_count"],
                    "unmatched_count": report["unmatched_count"],
                }
            )
        )
        return

    print(json.dumps(output, indent=2))

    # Optional write (use deduped list)
    if args.write and deduped:
        to_write = list(deduped.values())
        if args.max_write and args.max_write > 0:
            to_write = to_write[: args.max_write]
        client.add_receipt_metadatas(to_write)
        print(
            f"Wrote {len(to_write)} ReceiptMetadata records to DynamoDB table {DDB_TABLE}."
        )


if __name__ == "__main__":
    main()
