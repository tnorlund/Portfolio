import json
import re
from typing import Dict, List, Set, Tuple, Union
from pathlib import Path
from urllib.parse import urlparse

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.data._pulumi import load_env

from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.vector_store import VectorClient

words_export = Path(__file__).parent / "dev.words.ndjson"
lines_export = Path(__file__).parent / "dev.lines.ndjson"
chroma_words_dir = (Path(__file__).parent / "dev.chroma_words").resolve()
chroma_lines_dir = (Path(__file__).parent / "dev.chroma_lines").resolve()

dynamo_client = DynamoClient(load_env().get("dynamodb_table_name"))
chroma_bucket = load_env().get("chromadb_bucket_name")


def to_line_chroma_id(item: Union[ReceiptWord, ReceiptLine]) -> str:
    return f"IMAGE#{item.image_id}#RECEIPT#{item.receipt_id:05d}#LINE#{item.line_id:05d}"


def to_word_chroma_id(item: Union[ReceiptWord, ReceiptLine]) -> str:
    return f"IMAGE#{item.image_id}#RECEIPT#{item.receipt_id:05d}#LINE#{item.line_id:05d}#WORD#{item.word_id:05d}"


# If the files do not exist export it from DynamoDB using the client
if not words_export.exists() and not lines_export.exists():
    # Export the words and lines from DynamoDB
    words, _ = dynamo_client.list_receipt_words()
    lines, _ = dynamo_client.list_receipt_lines()

    # Save the words and lines to the files
    if words_export.exists():
        words_export.unlink()
    with open(words_export, "w", encoding="utf-8") as f:
        for word in words:
            f.write(json.dumps(dict(word)) + "\n")

    if lines_export.exists():
        lines_export.unlink()
    with open(lines_export, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(dict(line)) + "\n")
# If the files exist, load them
else:
    with open(words_export, "r", encoding="utf-8") as f:
        words = [ReceiptWord(**json.loads(line)) for line in f]

    with open(lines_export, "r", encoding="utf-8") as f:
        lines = [ReceiptLine(**json.loads(line)) for line in f]


local_lines_path = str(chroma_lines_dir)
local_words_path = str(chroma_words_dir)
use_existing_lines = chroma_lines_dir.exists() and any(
    chroma_lines_dir.rglob("*")
)
use_existing_words = chroma_words_dir.exists() and any(
    chroma_words_dir.rglob("*")
)

if not use_existing_lines or not use_existing_words:
    if not chroma_bucket:
        raise ValueError(
            "No chromadb_bucket_name configured and no local lines or words snapshot found; cannot download."
        )
    try:
        print(
            f"Downloading lines snapshot from s3://{chroma_bucket}/lines/snapshot/... to {local_lines_path}"
        )
        _ = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="lines",
            local_path=local_lines_path,
        )
        print(
            f"Downloading words snapshot from s3://{chroma_bucket}/words/snapshot/... to {local_words_path}"
        )
        _ = download_snapshot_atomic(
            bucket=chroma_bucket,
            collection="words",
            local_path=local_words_path,
        )
    except Exception as e:  # pylint: disable=broad-except
        raise ValueError(
            f"Snapshot (lines) download failed and no local snapshot present: {e}"
        )


line_client = VectorClient.create_line_client(
    persist_directory=local_lines_path, mode="read"
)
words_client = VectorClient.create_word_client(
    persist_directory=local_words_path, mode="read"
)


words_with_extracted_data = [
    w
    for w in words
    if w.extracted_data
    and w.extracted_data["type"] in ["address", "phone", "url"]
]


# ---------------- Normalization helpers ----------------
def _normalize_phone(text: str) -> str:
    digits = re.sub(r"\D+", "", text or "")
    if not digits:
        return ""
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


_STREET_ABBR = {
    "STREET": "ST",
    "ST": "ST",
    "ROAD": "RD",
    "RD": "RD",
    "AVENUE": "AVE",
    "AVE": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "DRIVE": "DR",
    "DR": "DR",
    "LANE": "LN",
    "LN": "LN",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "SUITE": "STE",
    "STE": "STE",
    "APARTMENT": "APT",
    "APT": "APT",
    "UNIT": "UNIT",
}

_SUITE_TOKENS = {"STE", "SUITE", "APT", "UNIT", "#"}
_STREET_SUFFIX_TOKENS = {"ST", "RD", "AVE", "BLVD", "DR", "LN", "HWY", "PKWY"}

# Basic set of US state/territory abbreviations for light validation
_STATE_ABBR = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC", "PR", "GU", "VI", "AS", "MP",
}


def _normalize_address(text: str) -> str:
    if not text:
        return ""
    # Minimal normalization: uppercase, collapse whitespace, trim edge punctuation
    t = str(text).upper()
    # Replace tabs and multiple spaces with single space
    t = re.sub(r"\s+", " ", t)
    # Trim common leading/trailing punctuation without removing interior chars
    t = t.strip(" ,.;:|/\\-\t")
    # Tokenize on spaces, preserve suite tokens and words; do NOT drop words
    tokens = [tok for tok in t.split(" ") if tok]
    normalized_tokens: List[str] = []
    for tok in tokens:
        tok2 = tok.strip(",.;:|/\\-")
        # Map full-word suffixes only (BOULEVARD->BLVD), never drop names
        normalized_tokens.append(_STREET_ABBR.get(tok2, tok2))
    return " ".join(normalized_tokens)


def _normalize_url(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    if not re.match(r"^[a-zA-Z]+://", t):
        t = "http://" + t
    try:
        parsed = urlparse(t)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (parsed.path or "").rstrip("/")
        return host + path
    except Exception:
        return text.strip().lower()


def _strip_phone_like(text: str) -> str:
    # No longer used for addresses; keep for potential future safeguards
    if not text:
        return ""
    t = str(text)
    t = re.sub(r"\+?\d?[\s\-.()]*\d{3}[\s\-.()]*\d{3}[\s\-.()]*\d{4}", " ", t)
    t = re.sub(r"\d{10,}", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_by_type(text: str, value_type: str) -> str:
    if value_type == "phone":
        return _normalize_phone(text)
    if value_type == "address":
        return _normalize_address(text)
    if value_type == "url":
        return _normalize_url(text)
    return (text or "").strip().upper()


# ---------------- Value validation helpers ----------------
def _is_valid_normalized(
    value_type: str, normalized: str, original_text: str
) -> bool:
    if not normalized:
        return False
    if value_type == "phone":
        # Require full US-length numbers to avoid noise
        return len(normalized) >= 10
    if value_type == "url":
        # Exclude emails and require a path to reduce generic domain noise
        if "@" in normalized:
            return False
        return "/" in normalized
    if value_type == "address":
        # Minimal validation: require at least 2 tokens (to avoid single ZIP-only)
        tokens = [tok for tok in normalized.split(" ") if tok]
        return len(tokens) >= 2
    return True


def _is_plausible_phone(digits: str) -> bool:
    if len(digits) < 10:
        return False
    if digits.startswith("000"):
        return False
    # Reject long runs of the same digit (e.g., 0000000000, 1111111111)
    if re.match(r"^(\d)\1{6,}\d*$", digits):
        return False
    return True


def _is_plausible_full_address(addr_norm: str) -> bool:
    if not addr_norm:
        return False
    tokens = [t for t in addr_norm.split(" ") if t]
    if len(tokens) < 3:
        return False
    # Identify zip tokens
    zip_tokens = [t for t in tokens if re.fullmatch(r"\d{5}", t)]
    # Street number must not be the zip
    has_street_num = any(
        re.fullmatch(r"\d{1,6}", t) and t not in zip_tokens for t in tokens
    )
    has_suffix = any(t in _STREET_SUFFIX_TOKENS for t in tokens)
    has_alpha_non_suffix = any(
        (
            t.isalpha()
            and t not in _STREET_SUFFIX_TOKENS
            and t not in _SUITE_TOKENS
        )
        for t in tokens
    )
    return has_street_num and has_suffix and has_alpha_non_suffix


def _looks_like_street_line(text: str) -> bool:
    if not text:
        return False
    t = _normalize_address(text)
    tokens = [tok for tok in t.split(" ") if tok]
    if not tokens:
        return False
    has_num = any(re.fullmatch(r"\d{1,6}", tok) for tok in tokens)
    has_suffix = any(tok in _STREET_SUFFIX_TOKENS for tok in tokens)
    return has_num and has_suffix


# ---------------- Build line-type index ----------------
LineKey = Tuple[str, int, int]
line_types_by_key: Dict[LineKey, Set[str]] = {}
for w in words_with_extracted_data:
    key: LineKey = (str(w.image_id), int(w.receipt_id), int(w.line_id))
    if key not in line_types_by_key:
        line_types_by_key[key] = set()
    try:
        t = str(w.extracted_data.get("type", ""))
        if t:
            line_types_by_key[key].add(t)
    except Exception:
        pass


# ---------------- Prepare unique line ids and fetch embeddings ----------------
def _build_line_chroma_id(image_id: str, receipt_id: int, line_id: int) -> str:
    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


unique_line_keys: List[LineKey] = sorted(
    {
        (str(w.image_id), int(w.receipt_id), int(w.line_id))
        for w in words_with_extracted_data
    }
)

line_key_to_cid: Dict[LineKey, str] = {}
for img_id, rec_id, ln_id in unique_line_keys:
    line_key_to_cid[(img_id, rec_id, ln_id)] = _build_line_chroma_id(
        img_id, rec_id, ln_id
    )

embeddings_by_cid: Dict[str, List[float]] = {}
if line_key_to_cid:
    try:
        ids = [cid for cid in line_key_to_cid.values()]
        got = line_client.get_by_ids(
            collection_name="lines",
            ids=ids,
            include=["embeddings", "metadatas", "documents"],
        )
        for i, cid in enumerate(got.get("ids", [])):
            emb = got.get("embeddings", [None])[i]
            if emb is not None:
                embeddings_by_cid[cid] = emb
    except Exception as e:  # pylint: disable=broad-except
        raise RuntimeError(f"Failed to load line embeddings: {e}")


# Build a line text map from exports
line_text_by_key: Dict[LineKey, str] = {}
for ln in lines:
    try:
        k: LineKey = (str(ln.image_id), int(ln.receipt_id), int(ln.line_id))
        line_text_by_key[k] = str(ln.text or "")
    except Exception:
        continue


def _classify_line_type(key: LineKey) -> str:
    preferred = line_types_by_key.get(key, set())
    if "phone" in preferred:
        return "phone"
    if "address" in preferred:
        return "address"
    if "url" in preferred:
        return "url"
    return "other"


# ---------------- Query similar lines and group by normalized identity ----------------
GroupKey = Tuple[str, str]
groups: Dict[GroupKey, List[Dict]] = {}


def _add_occurrence(
    value_type: str, normalized_value: str, occurrence: Dict
) -> None:
    if not normalized_value:
        return
    key: GroupKey = (value_type, normalized_value)
    if key not in groups:
        groups[key] = []
    groups[key].append(occurrence)


for key in unique_line_keys:
    cid = line_key_to_cid.get(key)
    emb = embeddings_by_cid.get(cid)
    if emb is None:
        continue
    value_type = _classify_line_type(key)
    if value_type not in {"phone", "address", "url"}:
        continue
    q_text = line_text_by_key.get(key, "")
    normalized = _normalize_by_type(q_text, value_type)
    if not _is_valid_normalized(value_type, normalized, q_text):
        continue

    try:
        res = line_client.query(
            collection_name="lines",
            query_embeddings=[emb],
            n_results=25,
            include=["metadatas", "documents", "distances"],
        )
    except Exception:  # pylint: disable=broad-except
        continue

    metas = (res or {}).get("metadatas") or []
    docs = (res or {}).get("documents") or []
    dists = (res or {}).get("distances") or []
    if not metas:
        continue
    mlist = metas[0]
    dlist = docs[0] if docs else []
    distlist = dists[0] if dists else []

    for i in range(min(len(mlist), len(dlist))):
        md = mlist[i] or {}
        cand_text = str(dlist[i] or "")
        cand_norm = _normalize_by_type(cand_text, value_type)
        if cand_norm != normalized:
            continue
        occurrence = {
            "type": value_type,
            "normalized": normalized,
            "image_id": md.get("image_id"),
            "receipt_id": md.get("receipt_id"),
            "merchant_name": md.get("merchant_name"),
            "text": cand_text,
            "distance": (
                float(distlist[i])
                if i < len(distlist) and distlist[i] is not None
                else None
            ),
        }
        _add_occurrence(value_type, normalized, occurrence)


# ---------------- Emit clusters with >=2 unique receipts (dedup per receipt, keep best) ----------------
clusters: List[Dict] = []
for (vtype, norm), occs in groups.items():
    best_by_receipt: Dict[Tuple[str, int], Dict] = {}
    for o in occs:
        img = o.get("image_id")
        rid = o.get("receipt_id")
        if img is None or rid is None:
            continue
        rkey = (str(img), int(rid))
        prev = best_by_receipt.get(rkey)
        if prev is None:
            best_by_receipt[rkey] = o
        else:
            d_prev = prev.get("distance")
            d_cur = o.get("distance")
            if d_prev is None or (d_cur is not None and d_cur < d_prev):
                best_by_receipt[rkey] = o

    if len(best_by_receipt) < 2:
        continue
    deduped_samples = sorted(
        best_by_receipt.values(),
        key=lambda s: (
            float("inf") if s.get("distance") is None else s.get("distance")
        ),
    )[:5]
    clusters.append(
        {
            "type": vtype,
            "normalized": norm,
            "count": len(best_by_receipt),
            "receipts": sorted(list(best_by_receipt.keys())),
            "samples": deduped_samples,
        }
    )

clusters.sort(key=lambda c: (-int(c["count"]), c["type"], c["normalized"]))

# ---------------- Derive combined phone+address clusters (URL as optional evidence) ----------------
# Build per-receipt sets
from collections import defaultdict

receipt_phones: Dict[Tuple[str, int], Set[str]] = defaultdict(set)
receipt_addresses: Dict[Tuple[str, int], Set[str]] = defaultdict(set)
receipt_urls: Dict[Tuple[str, int], Set[str]] = defaultdict(set)

for (vtype, norm), occs in groups.items():
    for o in occs:
        rk = (
            (str(o.get("image_id")), int(o.get("receipt_id")))
            if o.get("image_id") is not None
            and o.get("receipt_id") is not None
            else None
        )
        if not rk:
            continue
        if vtype == "phone":
            receipt_phones[rk].add(norm)
        elif vtype == "address":
            receipt_addresses[rk].add(norm)
        elif vtype == "url":
            receipt_urls[rk].add(norm)

# Group by (phone,address) pair
combo_to_receipts: Dict[Tuple[str, str], Set[Tuple[str, int]]] = defaultdict(
    set
)
for rk, phones in receipt_phones.items():
    addrs = receipt_addresses.get(rk, set())
    for p in phones:
        for a in addrs:
            combo_to_receipts[(p, a)].add(rk)

same_store_clusters: List[Dict] = []
for (p, a), rset in combo_to_receipts.items():
    if len(rset) < 2:
        continue
    # URL evidence: union and intersection across receipts
    urls_list = [receipt_urls.get(rk, set()) for rk in rset]
    if urls_list:
        url_intersection = set(urls_list[0])
        for s in urls_list[1:]:
            url_intersection &= s
    else:
        url_intersection = set()
    same_store_clusters.append(
        {
            "phone": p,
            "address": a,
            "count": len(rset),
            "receipts": sorted(list(rset)),
            "shared_urls": sorted(list(url_intersection))[:5],
        }
    )

same_store_clusters.sort(
    key=lambda c: (-int(c["count"]), c["phone"], c["address"])
)

# ---------------- Direct per-receipt aggregation (multi-line address) ----------------
def _build_full_address_for_receipt(
    receipt_key: Tuple[str, int],
    address_line_entries: Dict[Tuple[str, int], List[Tuple[int, str]]],
) -> str:
    parts = sorted(
        address_line_entries.get(receipt_key, []), key=lambda x: x[0]
    )
    if not parts:
        return ""
    full_text = " ".join([txt for _, txt in parts])
    return _normalize_address(full_text)


# Collect address/phone/url lines per receipt
from collections import defaultdict as _dd

address_lines_by_receipt = _dd(list)
phones_by_receipt = _dd(set)
urls_by_receipt = _dd(set)

for (img_id, rec_id, ln_id), types in line_types_by_key.items():
    rk = (str(img_id), int(rec_id))
    txt = line_text_by_key.get((img_id, rec_id, ln_id), "")
    if "address" in types:
        address_lines_by_receipt[rk].append((int(ln_id), str(txt)))
    if "phone" in types:
        ph = _normalize_phone(str(txt))
        if len(ph) >= 10:
            phones_by_receipt[rk].add(ph)
    if "url" in types:
        urls_by_receipt[rk].add(_normalize_url(str(txt)))

# Prefer full address from extracted word values when present
address_values_by_receipt: Dict[Tuple[str, int], List[str]] = _dd(list)
for w in words_with_extracted_data:
    try:
        if w.extracted_data and w.extracted_data.get("type") == "address":
            val = str(w.extracted_data.get("value") or "").strip()
            if val:
                address_values_by_receipt[
                    (str(w.image_id), int(w.receipt_id))
                ].append(val)
    except Exception:
        continue

full_address_value_by_receipt: Dict[Tuple[str, int], str] = {}
for rk, vals in address_values_by_receipt.items():
    # choose the longest unique value as best candidate
    uniq = sorted(
        set(v.strip() for v in vals if v.strip()), key=len, reverse=True
    )
    if uniq:
        full_address_value_by_receipt[rk] = _normalize_address(uniq[0])

# Build combined clusters based on (phone, full_address)
direct_combo_to_receipts: Dict[Tuple[str, str], Set[Tuple[str, int]]] = _dd(
    set
)
for rk, phone_set in phones_by_receipt.items():
    # Prefer value-derived full address; fallback to concatenated address lines
    full_addr = full_address_value_by_receipt.get(
        rk
    ) or _build_full_address_for_receipt(rk, address_lines_by_receipt)
    # require a plausible full address to reduce noise
    if not _is_plausible_full_address(full_addr):
        continue
    for p in phone_set:
        if not _is_plausible_phone(p):
            continue
        direct_combo_to_receipts[(p, full_addr)].add(rk)

same_store_clusters_direct: List[Dict] = []
for (p, addr), rset in direct_combo_to_receipts.items():
    if len(rset) < 2:
        continue
    # URL intersection evidence
    url_sets = [urls_by_receipt.get(rk, set()) for rk in rset]
    shared = set(url_sets[0]) if url_sets else set()
    for s in url_sets[1:]:
        shared &= s
    same_store_clusters_direct.append(
        {
            "phone": p,
            "address": addr,
            "count": len(rset),
            "receipts": sorted(list(rset)),
            "shared_urls": sorted(list(shared))[:5],
        }
    )

same_store_clusters_direct.sort(
    key=lambda c: (-int(c["count"]), c["phone"], c["address"])
)

# ---------------- Enrich clusters with merchant names from embedding metadatas ----------------
from collections import defaultdict as _dd2

merchant_names_by_receipt = _dd2(set)
for occs in groups.values():
    for o in occs:
        img = o.get("image_id")
        rid = o.get("receipt_id")
        mname = (o.get("merchant_name") or "").strip()
        if img is None or rid is None or not mname:
            continue
        merchant_names_by_receipt[(str(img), int(rid))].add(mname)


def _consensus_merchant(receipts: list[Tuple[str, int]]) -> dict:
    counts: Dict[str, int] = {}
    for rk in receipts:
        for name in merchant_names_by_receipt.get(tuple(rk), set()):
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "consensus": ranked[0][0] if ranked else "",
        "support": ranked[0][1] if ranked else 0,
        "candidates": [n for n, _ in ranked[:5]],
    }


for c in same_store_clusters_direct:
    info = _consensus_merchant(c.get("receipts", []))
    c["merchant_consensus"] = info.get("consensus")
    c["merchant_support"] = info.get("support")
    c["merchant_candidates"] = info.get("candidates")

print(
    json.dumps(
        {
            "cluster_count": len(clusters),
            "clusters": clusters,
            "same_store_cluster_count": len(same_store_clusters),
            "same_store_clusters": same_store_clusters,
            "same_store_cluster_count_direct": len(same_store_clusters_direct),
            "same_store_clusters_direct": same_store_clusters_direct,
        },
        indent=2,
    )
)

# ---------------- CSV export for direct same-store clusters ----------------
try:
    import csv

    with open(
        "entity_resolution.same_store_clusters.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "phone",
                "address",
                "count",
                "merchant_consensus",
                "merchant_support",
                "merchant_unique_count",
                "merchant_candidates",
                "shared_urls",
                "receipts",
            ]
        )
        for c in same_store_clusters_direct:
            receipts = c.get("receipts", [])
            # compute unique merchant names across receipts
            uniq_merchants = set()
            for rk in receipts:
                for m in merchant_names_by_receipt.get(tuple(rk), set()):
                    uniq_merchants.add(m)
            writer.writerow(
                [
                    c.get("phone", ""),
                    c.get("address", ""),
                    int(c.get("count", 0)),
                    c.get("merchant_consensus", ""),
                    int(c.get("merchant_support", 0)),
                    len(uniq_merchants),
                    "; ".join(sorted(list(c.get("merchant_candidates", []))))[
                        :1024
                    ],
                    "; ".join(c.get("shared_urls", []))[:512],
                    json.dumps(receipts),
                ]
            )
    print("Wrote entity_resolution.same_store_clusters.csv")
except Exception as e:  # pylint: disable=broad-except
    print(f"CSV export failed: {e}")