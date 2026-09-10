"""Find receipts with no DATE word label and check OCR text for date-like strings."""
import json, re, sys, collections
from receipt_dynamo import DynamoClient

table = sys.argv[1]
out = sys.argv[2]
c = DynamoClient(table)

# 1. all receipts
receipts, lek = [], None
while True:
    page, lek = c.list_receipts(limit=500, last_evaluated_key=lek)
    receipts.extend(page)
    if not lek: break
all_keys = {(r.image_id, r.receipt_id): r for r in receipts}

# 2. receipts with any DATE label (any validation status)
date_by_receipt = collections.defaultdict(list)
lek = None
while True:
    page, lek = c.get_receipt_word_labels_by_label("DATE", limit=500, last_evaluated_key=lek)
    for l in page:
        date_by_receipt[(l.image_id, l.receipt_id)].append(str(l.validation_status))
    if not lek: break

missing = [k for k in all_keys if k not in date_by_receipt]
invalid_only = [k for k, v in date_by_receipt.items() if all(s == "INVALID" for s in v)]

DATE_RE = re.compile(
    r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(,?\s*\d{2,4})?|"
    r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{2,4}?)\b", re.I)

def words_text(image_id, receipt_id):
    words = c.list_receipt_words_from_receipt(image_id, receipt_id)
    words.sort(key=lambda w: (w.line_id, w.word_id))
    lines = collections.defaultdict(list)
    for w in words: lines[w.line_id].append(w.text)
    return [" ".join(lines[k]) for k in sorted(lines)]

def merchant(image_id, receipt_id):
    try:
        m = c.get_receipt_metadata(image_id, receipt_id)
        return m.canonical_merchant_name or m.merchant_name
    except Exception:
        return None

rows = []
for k in missing + invalid_only:
    lines = words_text(*k)
    hits = [ln for ln in lines if DATE_RE.search(ln)]
    r = all_keys[k]
    rows.append({
        "image_id": k[0], "receipt_id": k[1],
        "kind": "no_date_label" if k in missing else "invalid_date_labels_only",
        "merchant": merchant(*k),
        "n_lines": len(lines),
        "regex_date_hits": hits,
        "cdn_s3_key": getattr(r, "cdn_s3_key", None),
        "lines": lines,
    })

summary = {
    "table": table,
    "total_receipts": len(all_keys),
    "receipts_with_date_label": len([k for k in date_by_receipt if k in all_keys]),
    "receipts_missing_date_label": len(missing),
    "receipts_with_only_invalid_date_labels": len(invalid_only),
    "missing_with_regex_hit": sum(1 for r in rows if r["kind"]=="no_date_label" and r["regex_date_hits"]),
    "missing_without_regex_hit": sum(1 for r in rows if r["kind"]=="no_date_label" and not r["regex_date_hits"]),
}
json.dump({"summary": summary, "rows": rows}, open(out, "w"), indent=1)
print(json.dumps(summary, indent=1))
