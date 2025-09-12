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


# ---------------- Minimal helpers ----------------
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


def _normalize_address(text: str) -> str:
    if not text:
        return ""
    t = str(text).upper()
    t = re.sub(r"\s+", " ", t).strip(" ,.;:|/\\-\t")
    tokens = [tok for tok in t.split(" ") if tok]
    normalized_tokens: List[str] = []
    for tok in tokens:
        tok2 = tok.strip(",.;:|/\\-")
        normalized_tokens.append(_STREET_ABBR.get(tok2, tok2))
    return " ".join(normalized_tokens)


# ---------------- Build indices ----------------
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

line_text_by_key: Dict[LineKey, str] = {}
for ln in lines:
    try:
        k: LineKey = (str(ln.image_id), int(ln.receipt_id), int(ln.line_id))
        line_text_by_key[k] = str(ln.text or "")
    except Exception:
        continue


# ---------------- Aggregate per receipt ----------------
from collections import defaultdict as _dd

phones_by_receipt: Dict[Tuple[str, int], Set[str]] = _dd(set)
address_lines_by_receipt: Dict[Tuple[str, int], List[Tuple[int, str]]] = _dd(
    list
)
address_values_by_receipt: Dict[Tuple[str, int], List[str]] = _dd(list)

for w in words_with_extracted_data:
    rk = (str(w.image_id), int(w.receipt_id))
    if w.extracted_data.get("type") == "phone":
        ph = _normalize_phone(str(w.text))
        if len(ph) >= 10:
            phones_by_receipt[rk].add(ph)
    if w.extracted_data.get("type") == "address":
        val = str(w.extracted_data.get("value") or "").strip()
        if val:
            address_values_by_receipt[rk].append(val)

for (img_id, rec_id, ln_id), types in line_types_by_key.items():
    if "address" in types:
        rk = (str(img_id), int(rec_id))
        txt = line_text_by_key.get((img_id, rec_id, ln_id), "")
        address_lines_by_receipt[rk].append((int(ln_id), str(txt)))


def build_full_address(rk: Tuple[str, int]) -> str:
    vals = address_values_by_receipt.get(rk, [])
    if vals:
        uniq = sorted(
            set(v.strip() for v in vals if v.strip()), key=len, reverse=True
        )
        return _normalize_address(uniq[0])
    parts = sorted(address_lines_by_receipt.get(rk, []), key=lambda t: t[0])
    full_text = " ".join([t for _, t in parts]) if parts else ""
    return _normalize_address(full_text)


# ---------------- Build clusters: merge by full address (phones consolidated) ----------------
address_to_receipts: Dict[str, Set[Tuple[str, int]]] = _dd(set)
address_to_phones: Dict[str, List[str]] = _dd(list)

for rk, phones in phones_by_receipt.items():
    full_addr = build_full_address(rk)
    if not full_addr or len(full_addr.split(" ")) < 3:
        continue
    address_to_receipts[full_addr].add(rk)
    for p in phones:
        digits = _normalize_phone(p)
        if digits:
            address_to_phones[full_addr].append(digits)


def _canonical_phone(phones: List[str]) -> Tuple[str, Dict[str, int]]:
    counts: Dict[str, int] = {}
    for ph in phones:
        if len(ph) == 10 and not (ph.startswith("000") or ph == ph[0] * 10):
            counts[ph] = counts.get(ph, 0) + 1
    if not counts:
        return "", {}
    winner = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return winner, counts


same_store_clusters: List[Dict] = []
for addr, rset in address_to_receipts.items():
    if len(rset) < 2:
        continue
    phones = address_to_phones.get(addr, [])
    canon, counts = _canonical_phone(phones)
    aliases = sorted([p for p in set(phones) if p != canon])
    same_store_clusters.append(
        {
            "address": addr,
            "count": len(rset),
            "receipts": sorted(list(rset)),
            "phone_canonical": canon,
            "phone_aliases": aliases,
        }
    )

same_store_clusters.sort(
    key=lambda c: (
        -int(c["count"]),
        c.get("phone_canonical", ""),
        c["address"],
    )
)

print(
    json.dumps(
        {
            "same_store_cluster_count": len(same_store_clusters),
            "same_store_clusters": same_store_clusters,
        },
        indent=2,
    )
)

# CSV export
try:
    import csv

    with open(
        "clean_entity_resolution.same_store_clusters.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "address",
                "count",
                "phone_canonical",
                "phone_aliases",
                "receipts",
            ]
        )
        for c in same_store_clusters:
            writer.writerow(
                [
                    c.get("address", ""),
                    int(c.get("count", 0)),
                    c.get("phone_canonical", ""),
                    ",".join(c.get("phone_aliases", [])),
                    json.dumps(c.get("receipts", [])),
                ]
            )
    print("Wrote clean_entity_resolution.same_store_clusters.csv")
except Exception as e:  # pylint: disable=broad-except
    print(f"CSV export failed: {e}")

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


# Basic set of US state/territory abbreviations for light validation
_STATE_ABBR = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
    "PR",
    "GU",
    "VI",
    "AS",
    "MP",
}


def _normalize_address(text: str) -> str:
    if not text:
        return ""
    t = re.sub(
        r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", str(text).upper())
    ).strip()
    tokens = [tok for tok in t.split(" ") if tok]
    normalized_tokens: List[str] = []
    for tok in tokens:
        normalized_tokens.append(_STREET_ABBR.get(tok, tok))
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
    if not text:
        return ""
    t = str(text)
    # Remove common phone formats and long digit runs
    t = re.sub(r"\+?\d?[\s\-.()]*\d{3}[\s\-.()]*\d{3}[\s\-.()]*\d{4}", " ", t)
    t = re.sub(r"\d{10,}", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_by_type(text: str, value_type: str) -> str:
    if value_type == "phone":
        return _normalize_phone(text)
    if value_type == "address":
        return _normalize_address(_strip_phone_like(text))
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
        # Be permissive; rely on exact normalized line equality later
        tokens = [tok for tok in normalized.split(" ") if tok]
        return len(tokens) >= 2
    return True


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

print(
    json.dumps(
        {"cluster_count": len(clusters), "clusters": clusters}, indent=2
    )
)
