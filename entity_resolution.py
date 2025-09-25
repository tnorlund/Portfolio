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