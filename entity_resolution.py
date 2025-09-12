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
address_lines_by_receipt: Dict[Tuple[str, int], List[Tuple[int, str]]] = {}
phones_by_receipt: Dict[Tuple[str, int], Set[str]] = {}
urls_by_receipt: Dict[Tuple[str, int], Set[str]] = {}

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
        {"cluster_count": len(clusters), "clusters": clusters}, indent=2
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
from typing import Union
from pathlib import Path

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
chroma_bucket = load_env().get("chroma_bucket")


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
if not chroma_bucket:
    raise ValueError(
        "No VECTORS_BUCKET configured and no local lines or words snapshot found; cannot download."
    )

if not use_existing_lines or not use_existing_words:
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
    if w.extracted_data and w.extracted_data["type"] in ["address", "phone"]
]
