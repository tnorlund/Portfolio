#!/usr/bin/env python3
"""
Dev script: Debug ChromaDB Evidence Flow

Diagnoses why query_label_evidence() returns empty evidence lists
even when Chroma Cloud is connected and collections are accessible.

Three diagnostic phases:
  Phase 1 — Probe Chroma Cloud directly (collections, counts, word lookup)
  Phase 2 — Full query_label_evidence flow with verbose logging
  Phase 3 — End-to-end evidence pipeline (if words exist)

Run with:
    python infra/label_evaluator_step_functions/dev_test_evidence.py

Requires:
    LANGCHAIN_API_KEY, OPENROUTER_API_KEY in env
    Pulumi dev stack deployed (for Chroma Cloud + DynamoDB config)
"""

import json
import logging
import os
import subprocess
import sys
import types

# ---------------------------------------------------------------------------
# Path setup (same as dev_test_traceable.py)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas", "utils"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambdas"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# ---------------------------------------------------------------------------
# Module stubs (same as dev_test_traceable.py)
# ---------------------------------------------------------------------------
for mod_name, mod_path in [
    ("receipt_agent", "receipt_agent/receipt_agent"),
    ("receipt_agent.utils", "receipt_agent/receipt_agent/utils"),
]:
    stub = types.ModuleType(mod_name)
    stub.__path__ = [mod_path]
    sys.modules[mod_name] = stub

# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------
if not os.environ.get("LANGCHAIN_API_KEY"):
    print("ERROR: LANGCHAIN_API_KEY environment variable must be set")
    sys.exit(1)

if not os.environ.get("OPENROUTER_API_KEY"):
    print("ERROR: OPENROUTER_API_KEY environment variable must be set")
    sys.exit(1)

os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGCHAIN_PROJECT"] = "dev-evidence-debug"

# ---------------------------------------------------------------------------
# Load Pulumi config for Chroma Cloud + DynamoDB
# ---------------------------------------------------------------------------
INFRA_DIR = os.path.join(os.path.dirname(__file__), "..")


def _pulumi_config_get(key: str, stack: str = "dev") -> str:
    """Get a Pulumi config value."""
    try:
        result = subprocess.run(
            ["pulumi", "config", "get", key, "--stack", stack],
            capture_output=True,
            text=True,
            cwd=INFRA_DIR,
            check=False,
        )
        return result.stdout.strip()
    except Exception as exc:
        print(f"  WARNING: Could not get pulumi config {key}: {exc}")
        return ""


def _pulumi_stack_output(key: str, stack: str = "dev") -> str:
    """Get a Pulumi stack output value."""
    try:
        result = subprocess.run(
            ["pulumi", "stack", "output", key, "--stack", stack],
            capture_output=True,
            text=True,
            cwd=INFRA_DIR,
            check=False,
        )
        return result.stdout.strip()
    except Exception as exc:
        print(f"  WARNING: Could not get pulumi output {key}: {exc}")
        return ""


print("=" * 70)
print("DEV TEST: Debug ChromaDB Evidence Flow")
print("=" * 70)
print()

print("Loading Pulumi config...")
CHROMA_CLOUD_API_KEY = _pulumi_config_get("CHROMA_CLOUD_API_KEY")
CHROMA_CLOUD_TENANT = _pulumi_config_get("CHROMA_CLOUD_TENANT")
CHROMA_CLOUD_DATABASE = _pulumi_config_get("CHROMA_CLOUD_DATABASE")
DYNAMO_TABLE_NAME = _pulumi_stack_output("dynamodb_table_name")

print(f"  CHROMA_CLOUD_TENANT   = {CHROMA_CLOUD_TENANT or '(empty)'}")
print(f"  CHROMA_CLOUD_DATABASE = {CHROMA_CLOUD_DATABASE or '(empty)'}")
print(f"  CHROMA_CLOUD_API_KEY  = {'***' + CHROMA_CLOUD_API_KEY[-4:] if len(CHROMA_CLOUD_API_KEY) > 4 else '(empty)'}")
print(f"  DYNAMO_TABLE_NAME     = {DYNAMO_TABLE_NAME or '(empty)'}")
print()

if not CHROMA_CLOUD_API_KEY:
    print("ERROR: CHROMA_CLOUD_API_KEY not found in Pulumi config")
    sys.exit(1)

if not DYNAMO_TABLE_NAME:
    print("ERROR: dynamodb_table_name not found in Pulumi stack outputs")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Imports (after path setup)
# ---------------------------------------------------------------------------
from receipt_chroma import ChromaClient
from receipt_dynamo import DynamoClient
from receipt_agent.utils.chroma_helpers import (
    LabelEvidence,
    build_line_chroma_id,
    build_word_chroma_id,
    compute_label_consensus,
    query_label_evidence,
)

# Enable verbose logging for chroma_helpers
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
chroma_logger = logging.getLogger("receipt_agent.utils.chroma_helpers")
chroma_logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# PHASE 1: Probe Chroma Cloud directly
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("PHASE 1: Probe Chroma Cloud directly")
print("=" * 70)
print()

chroma_client = ChromaClient(
    cloud_api_key=CHROMA_CLOUD_API_KEY,
    cloud_tenant=CHROMA_CLOUD_TENANT or None,
    cloud_database=CHROMA_CLOUD_DATABASE or None,
    mode="read",
    metadata_only=True,
)

# List collections
try:
    collections = chroma_client.list_collections()
    print(f"  Collections: {collections}")
except Exception as exc:
    print(f"  ERROR listing collections: {exc}")
    collections = []

# Count items in each collection
for coll_name in collections:
    try:
        count = chroma_client.count(coll_name)
        print(f"  Collection '{coll_name}': {count} items")
    except Exception as exc:
        print(f"  ERROR counting '{coll_name}': {exc}")

# Connect to DynamoDB and find a real receipt to test with
print()
print("  Fetching a receipt from DynamoDB for testing...")
dynamo = DynamoClient(table_name=DYNAMO_TABLE_NAME)

# Get some word labels to find a receipt with labeled data
try:
    labels, _ = dynamo.list_receipt_word_labels(limit=50)
    if not labels:
        print("  ERROR: No receipt word labels found in DynamoDB")
        sys.exit(1)

    # Pick the first receipt that has a GRAND_TOTAL label (interesting for testing)
    test_label = None
    for lbl in labels:
        if lbl.label == "GRAND_TOTAL":
            test_label = lbl
            break
    if test_label is None:
        # Fall back to any label
        test_label = labels[0]

    TEST_IMAGE_ID = test_label.image_id
    TEST_RECEIPT_ID = test_label.receipt_id
    TEST_LINE_ID = test_label.line_id
    TEST_WORD_ID = test_label.word_id
    TEST_LABEL = test_label.label

    print(f"  Test receipt: image_id={TEST_IMAGE_ID}, receipt_id={TEST_RECEIPT_ID}")
    print(f"  Test word: line_id={TEST_LINE_ID}, word_id={TEST_WORD_ID}, label={TEST_LABEL}")
except Exception as exc:
    print(f"  ERROR fetching labels: {exc}")
    sys.exit(1)

# Build the ChromaDB ID and probe for this word
word_chroma_id = build_word_chroma_id(
    TEST_IMAGE_ID, TEST_RECEIPT_ID, TEST_LINE_ID, TEST_WORD_ID
)
print(f"\n  Word ChromaDB ID: {word_chroma_id}")

try:
    result = chroma_client.get(
        collection_name="words",
        ids=[word_chroma_id],
        include=["embeddings", "metadatas"],
    )
    embeddings = result.get("embeddings")
    metadatas = result.get("metadatas")
    ids_returned = result.get("ids")

    print(f"  IDs returned: {ids_returned}")
    print(f"  Embeddings count: {len(embeddings) if embeddings else 0}")
    print(f"  Metadatas count: {len(metadatas) if metadatas else 0}")

    if embeddings is not None and len(embeddings) > 0:
        emb = embeddings[0]
        if hasattr(emb, "tolist"):
            emb = emb.tolist()
        if isinstance(emb, list) and len(emb) > 0:
            print(f"  Embedding dims: {len(emb)}, first 5: {emb[:5]}")
            WORD_EMBEDDING_FOUND = True
        else:
            print(f"  WARNING: Embedding is empty or invalid: {type(emb)}")
            WORD_EMBEDDING_FOUND = False
    else:
        print("  WARNING: No embedding returned for this word")
        WORD_EMBEDDING_FOUND = False

    if metadatas is not None and len(metadatas) > 0:
        meta = metadatas[0]
        print(f"  Metadata keys: {sorted(meta.keys()) if meta else '(empty)'}")
        # Check for label_* boolean fields
        label_fields = {k: v for k, v in meta.items() if k.startswith("label_")}
        print(f"  Label fields: {label_fields}")
    else:
        print("  No metadata returned")

except Exception as exc:
    print(f"  ERROR getting word from Chroma: {exc}")
    WORD_EMBEDDING_FOUND = False

# Also probe the line embedding
line_chroma_id = build_line_chroma_id(
    TEST_IMAGE_ID, TEST_RECEIPT_ID, TEST_LINE_ID
)
print(f"\n  Line ChromaDB ID: {line_chroma_id}")

try:
    line_result = chroma_client.get(
        collection_name="lines",
        ids=[line_chroma_id],
        include=["embeddings", "metadatas"],
    )
    line_embeddings = line_result.get("embeddings")
    line_ids_returned = line_result.get("ids")
    print(f"  Line IDs returned: {line_ids_returned}")
    print(f"  Line embeddings count: {len(line_embeddings) if line_embeddings else 0}")

    if line_embeddings is not None and len(line_embeddings) > 0:
        le = line_embeddings[0]
        if hasattr(le, "tolist"):
            le = le.tolist()
        if isinstance(le, list) and len(le) > 0:
            print(f"  Line embedding dims: {len(le)}")
            LINE_EMBEDDING_FOUND = True
        else:
            print(f"  WARNING: Line embedding empty or invalid: {type(le)}")
            LINE_EMBEDDING_FOUND = False
    else:
        print("  WARNING: No line embedding returned")
        LINE_EMBEDDING_FOUND = False
except Exception as exc:
    print(f"  ERROR getting line from Chroma: {exc}")
    LINE_EMBEDDING_FOUND = False

# ---------------------------------------------------------------------------
# PHASE 2: Full query_label_evidence flow with verbose logging
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("PHASE 2: Full query_label_evidence flow")
print("=" * 70)
print()

# Get merchant name from ReceiptPlace
merchant_name = "Unknown"
try:
    place = dynamo.get_receipt_place(TEST_IMAGE_ID, TEST_RECEIPT_ID)
    merchant_name = place.merchant_name or "Unknown"
    print(f"  Merchant: {merchant_name}")
except Exception as exc:
    print(f"  WARNING: Could not get ReceiptPlace: {exc}")
    print(f"  Using merchant_name='{merchant_name}'")

print(f"\n  Calling query_label_evidence(")
print(f"    image_id={TEST_IMAGE_ID},")
print(f"    receipt_id={TEST_RECEIPT_ID},")
print(f"    line_id={TEST_LINE_ID},")
print(f"    word_id={TEST_WORD_ID},")
print(f"    target_label={TEST_LABEL},")
print(f"    target_merchant={merchant_name},")
print(f"  )")
print()

evidence = query_label_evidence(
    chroma_client=chroma_client,
    image_id=TEST_IMAGE_ID,
    receipt_id=TEST_RECEIPT_ID,
    line_id=TEST_LINE_ID,
    word_id=TEST_WORD_ID,
    target_label=TEST_LABEL,
    target_merchant=merchant_name,
    n_results_per_query=15,
    min_similarity=0.70,
    include_collections=("words", "lines"),
)

print(f"\n  Evidence returned: {len(evidence)} items")
if evidence:
    for i, e in enumerate(evidence[:10]):
        print(
            f"    [{i}] text='{e.word_text}' sim={e.similarity_score:.3f} "
            f"valid={e.label_valid} merchant='{e.merchant_name}' "
            f"same_merchant={e.is_same_merchant} source={e.evidence_source}"
        )

    consensus, pos_count, neg_count = compute_label_consensus(evidence)
    print(f"\n  Consensus: {consensus:.3f} (positive={pos_count}, negative={neg_count})")
else:
    print("  No evidence found — this confirms the pipeline returns empty.")

# ---------------------------------------------------------------------------
# Additional diagnostics: try a raw query to see what Chroma returns
# ---------------------------------------------------------------------------
if WORD_EMBEDDING_FOUND:
    print()
    print("-" * 50)
    print("  Additional: Raw Chroma query with label filter")
    print("-" * 50)

    # Re-fetch the embedding for the raw query
    try:
        raw_result = chroma_client.get(
            collection_name="words",
            ids=[word_chroma_id],
            include=["embeddings"],
        )
        raw_emb = raw_result["embeddings"][0]
        if hasattr(raw_emb, "tolist"):
            raw_emb = raw_emb.tolist()

        label_field = f"label_{TEST_LABEL}"

        # Try querying with label_GRAND_TOTAL=True
        print(f"\n  Query: where={{{label_field}: True}}")
        try:
            q_true = chroma_client.query(
                collection_name="words",
                query_embeddings=[raw_emb],
                n_results=5,
                where={label_field: True},
                include=["metadatas", "distances"],
            )
            true_ids = q_true.get("ids", [[]])[0]
            true_dists = q_true.get("distances", [[]])[0]
            print(f"    Results: {len(true_ids)} items")
            for tid, td in zip(true_ids[:3], true_dists[:3]):
                sim = max(0.0, 1.0 - (float(td) / 2.0))
                print(f"      id={tid[:60]}... dist={td:.4f} sim={sim:.3f}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # Try querying with label_GRAND_TOTAL=False
        print(f"\n  Query: where={{{label_field}: False}}")
        try:
            q_false = chroma_client.query(
                collection_name="words",
                query_embeddings=[raw_emb],
                n_results=5,
                where={label_field: False},
                include=["metadatas", "distances"],
            )
            false_ids = q_false.get("ids", [[]])[0]
            false_dists = q_false.get("distances", [[]])[0]
            print(f"    Results: {len(false_ids)} items")
            for fid, fd in zip(false_ids[:3], false_dists[:3]):
                sim = max(0.0, 1.0 - (float(fd) / 2.0))
                print(f"      id={fid[:60]}... dist={fd:.4f} sim={sim:.3f}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # Try querying WITHOUT any label filter (to see if embeddings exist at all)
        print(f"\n  Query: no where filter (raw similarity)")
        try:
            q_raw = chroma_client.query(
                collection_name="words",
                query_embeddings=[raw_emb],
                n_results=5,
                include=["metadatas", "distances"],
            )
            raw_ids = q_raw.get("ids", [[]])[0]
            raw_dists = q_raw.get("distances", [[]])[0]
            raw_metas = q_raw.get("metadatas", [[]])[0]
            print(f"    Results: {len(raw_ids)} items")
            for rid, rd, rm in zip(raw_ids[:3], raw_dists[:3], raw_metas[:3]):
                sim = max(0.0, 1.0 - (float(rd) / 2.0))
                label_fields = {k: v for k, v in rm.items() if k.startswith("label_")} if rm else {}
                print(f"      id={rid[:60]}... dist={rd:.4f} sim={sim:.3f}")
                print(f"        label_fields={label_fields}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

    except Exception as exc:
        print(f"  ERROR during raw query: {exc}")

# ---------------------------------------------------------------------------
# PHASE 3: End-to-end (only if Phase 2 found evidence)
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("PHASE 3: End-to-end evidence pipeline")
print("=" * 70)
print()

if evidence:
    print("  Evidence was found in Phase 2 — the pipeline works!")
    print("  The 'total_evidence_items: 0' issue may be fixed by the numpy")
    print("  truthiness patches or is in a different code path.")
else:
    if not WORD_EMBEDDING_FOUND and not LINE_EMBEDDING_FOUND:
        print("  DIAGNOSIS: Words/lines from this receipt do NOT exist in Chroma Cloud.")
        print("  Root cause: Embeddings were never ingested into the cloud database.")
        print()
        print("  Next steps:")
        print("    1. Check if the chroma-ingest pipeline has run for this receipt")
        print("    2. Verify the ingest pipeline targets the correct Chroma Cloud")
        print(f"       database ({CHROMA_CLOUD_DATABASE or 'default'})")
        print("    3. Run the ingest pipeline for this receipt's data")
    elif WORD_EMBEDDING_FOUND:
        print("  DIAGNOSIS: Word embedding EXISTS but query_label_evidence returned empty.")
        print("  This means the label_* boolean metadata fields are missing or the")
        print("  where filter doesn't match any records.")
        print()
        print("  Check the raw query results above to confirm:")
        print("    - If 'no where filter' returns results but label filter doesn't,")
        print("      the label_* metadata fields are missing from ingested data")
        print("    - If 'no where filter' also returns 0, the collection may be empty")
    else:
        print("  DIAGNOSIS: Word embedding not found, but line embedding may exist.")
        print("  The evidence pipeline needs word embeddings to function.")
        print("  Check whether the words collection was populated.")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("DIAGNOSTIC SUMMARY")
print("=" * 70)
print(f"  Chroma Cloud tenant:    {CHROMA_CLOUD_TENANT or 'default'}")
print(f"  Chroma Cloud database:  {CHROMA_CLOUD_DATABASE or 'default'}")
print(f"  Collections found:      {collections}")
for coll_name in collections:
    try:
        count = chroma_client.count(coll_name)
        print(f"    {coll_name}: {count} items")
    except Exception:
        pass
print(f"  Test receipt:           {TEST_IMAGE_ID}:{TEST_RECEIPT_ID}")
print(f"  Test word:              line={TEST_LINE_ID}, word={TEST_WORD_ID}, label={TEST_LABEL}")
print(f"  Word embedding found:   {WORD_EMBEDDING_FOUND}")
print(f"  Line embedding found:   {LINE_EMBEDDING_FOUND}")
print(f"  Evidence items:         {len(evidence)}")
if evidence:
    consensus, pos_count, neg_count = compute_label_consensus(evidence)
    print(f"  Consensus score:        {consensus:.3f} (+{pos_count}/-{neg_count})")
print()

if not evidence:
    if not WORD_EMBEDDING_FOUND:
        print("  RESULT: FAILURE POINT = Word not in Chroma Cloud")
        print("  ACTION: Ingest receipt embeddings into Chroma Cloud")
    else:
        print("  RESULT: FAILURE POINT = Label metadata filter mismatch")
        print("  ACTION: Check ingestion pipeline writes label_* boolean fields")
else:
    print("  RESULT: Evidence pipeline is working")
