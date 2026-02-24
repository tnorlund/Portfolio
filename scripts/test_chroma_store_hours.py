#!/usr/bin/env python3
"""
Test ChromaDB STORE_HOURS consensus query fix.

Confirms that the words collection uses array metadata (valid_labels_array)
rather than boolean flags (label_STORE_HOURS), and that the current consensus
query in chroma_helpers.py returns 0 results because of this mismatch.

Usage:
    /usr/local/bin/python3.12 scripts/test_chroma_store_hours.py
"""

import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))

from receipt_dynamo.data._pulumi import load_secrets

try:
    import chromadb
except ImportError:
    print("ERROR: chromadb not installed. Use /usr/local/bin/python3.12")
    sys.exit(1)


def main():
    print("Loading Pulumi secrets (dev)...")
    secrets = load_secrets(env="dev")

    api_key = secrets["portfolio:CHROMA_CLOUD_API_KEY"]
    tenant = secrets["portfolio:CHROMA_CLOUD_TENANT"]
    database = secrets["portfolio:CHROMA_CLOUD_DATABASE"]

    print(f"Connecting to Chroma Cloud (tenant={tenant}, db={database})...")
    client = chromadb.CloudClient(
        api_key=api_key,
        tenant=tenant,
        database=database,
    )

    collections = [c.name for c in client.list_collections()]
    print(f"Available collections: {collections}")

    # ── Test 1: Words collection with boolean flag (current broken query) ──
    print("\n" + "=" * 70)
    print("TEST 1: Words collection — where={label_STORE_HOURS: True}")
    print("Expected: 0 results (boolean field does not exist on words)")
    print("=" * 70)

    words = client.get_collection("words")
    print(f"Words collection total count: {words.count()}")

    try:
        result = words.get(
            where={"label_STORE_HOURS": True},
            limit=200,
            include=["metadatas"],
        )
        boolean_count = len(result["ids"])
        print(f"Results with label_STORE_HOURS=True: {boolean_count}")
        if boolean_count > 0:
            for i, (wid, meta) in enumerate(
                zip(result["ids"][:5], result["metadatas"][:5])
            ):
                # Show all metadata keys to understand why boolean exists
                label_keys = [k for k in meta if k.startswith("label_")]
                array_keys = [k for k in meta if "array" in k]
                print(f"  [{i}] id={wid} text={meta.get('text', '?')}")
                print(f"       label_* keys: {label_keys}")
                print(f"       array keys: {array_keys} vals: {[meta.get(k) for k in array_keys]}")
    except Exception as e:
        boolean_count = 0
        print(f"Query failed (expected): {e}")

    # ── Test 2: Words collection with $contains (correct query) ──
    print("\n" + "=" * 70)
    print('TEST 2: Words collection — where={valid_labels_array: {$contains: "STORE_HOURS"}}')
    print("Expected: many results")
    print("=" * 70)

    try:
        result = words.get(
            where={"valid_labels_array": {"$contains": "STORE_HOURS"}},
            limit=200,
            include=["metadatas"],
        )
        contains_count = len(result["ids"])
        print(f"Results with valid_labels_array $contains STORE_HOURS: {contains_count}")
        if contains_count > 0:
            merchants = {}
            text_counts = {}
            for meta in result["metadatas"]:
                merchant = meta.get("merchant_name", "Unknown")
                merchants[merchant] = merchants.get(merchant, 0) + 1
                t = meta.get("text", "?")
                text_counts[t] = text_counts.get(t, 0) + 1
            print(f"\nTop word texts ({len(text_counts)} unique):")
            for t, c in sorted(text_counts.items(), key=lambda x: -x[1])[:15]:
                print(f"  {t:20s} x{c}")
            print(f"\nMerchants with STORE_HOURS words ({len(merchants)} unique):")
            for m, c in sorted(merchants.items(), key=lambda x: -x[1])[:10]:
                print(f"  {m}: {c} words")
    except Exception as e:
        contains_count = 0
        print(f"Query failed: {e}")

    # ── Test 3: Also check invalid_labels_array ──
    print("\n" + "=" * 70)
    print('TEST 3: Words collection — invalid_labels_array $contains STORE_HOURS')
    print("=" * 70)

    try:
        result = words.get(
            where={"invalid_labels_array": {"$contains": "STORE_HOURS"}},
            limit=200,
            include=["metadatas"],
        )
        invalid_count = len(result["ids"])
        print(f"Results with invalid_labels_array $contains STORE_HOURS: {invalid_count}")
        if invalid_count > 0:
            inv_texts = {}
            for meta in result["metadatas"]:
                t = meta.get("text", "?")
                inv_texts[t] = inv_texts.get(t, 0) + 1
            print(f"\nTop invalid STORE_HOURS texts ({len(inv_texts)} unique):")
            for t, c in sorted(inv_texts.items(), key=lambda x: -x[1])[:10]:
                print(f"  {t:20s} x{c}")
    except Exception as e:
        invalid_count = 0
        print(f"Query failed: {e}")

    # ── Test 4: Lines collection with boolean flag (should work) ──
    print("\n" + "=" * 70)
    print("TEST 4: Lines collection — where={label_STORE_HOURS: True}")
    print("Expected: some results (lines collection uses boolean flags)")
    print("=" * 70)

    lines = client.get_collection("lines")
    print(f"Lines collection total count: {lines.count()}")

    try:
        result = lines.get(
            where={"label_STORE_HOURS": True},
            limit=100,
            include=["metadatas"],
        )
        lines_bool_count = len(result["ids"])
        print(f"Results with label_STORE_HOURS=True: {lines_bool_count}")
        if lines_bool_count > 0:
            for meta in result["metadatas"][:5]:
                text = meta.get("text", meta.get("line_text", "?"))
                print(f"  text={text} merchant={meta.get('merchant_name', '?')}")
    except Exception as e:
        lines_bool_count = 0
        print(f"Query failed: {e}")

    # ── Test 5: Consensus-style similarity query with $contains ──
    print("\n" + "=" * 70)
    print("TEST 5: Consensus-style similarity query for a STORE_HOURS word")
    print("=" * 70)

    try:
        # Get a STORE_HOURS word with its embedding
        sample = words.get(
            where={"valid_labels_array": {"$contains": "STORE_HOURS"}},
            limit=1,
            include=["metadatas", "embeddings"],
        )
        has_embeddings = (
            sample["ids"]
            and sample["embeddings"] is not None
            and len(sample["embeddings"]) > 0
        )
        if has_embeddings:
            sample_id = sample["ids"][0]
            sample_meta = sample["metadatas"][0]
            sample_embedding = list(sample["embeddings"][0])  # numpy→list
            print(f"Query word: text={sample_meta.get('text', '?')} "
                  f"merchant={sample_meta.get('merchant_name', '?')}")

            # Query for similar words with the CORRECT $contains filter
            sim_result = words.query(
                query_embeddings=[sample_embedding],
                n_results=20,
                where={"valid_labels_array": {"$contains": "STORE_HOURS"}},
                include=["metadatas", "distances"],
            )
            metadatas = sim_result["metadatas"][0] if sim_result["metadatas"] else []
            distances = sim_result["distances"][0] if sim_result["distances"] else []

            print(f"\nSimilar STORE_HOURS words ({len(metadatas)} results):")
            for meta, dist in zip(metadatas[:10], distances[:10]):
                similarity = max(0.0, 1.0 - (dist / 2.0))
                print(f"  sim={similarity:.3f} text={meta.get('text', '?'):20s} "
                      f"merchant={meta.get('merchant_name', '?')}")

            # Now try the BROKEN boolean query for comparison
            try:
                broken_result = words.query(
                    query_embeddings=[sample_embedding],
                    n_results=20,
                    where={"label_STORE_HOURS": True},
                    include=["metadatas", "distances"],
                )
                broken_count = len(broken_result["metadatas"][0]) if broken_result["metadatas"] else 0
            except Exception:
                broken_count = 0
            print(f"\nSame query with broken boolean filter: {broken_count} results")
        else:
            print("No STORE_HOURS word found with embedding")
    except Exception as e:
        print(f"Consensus query test failed: {e}")

    # ── Summary ──
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Words — label_STORE_HOURS=True (boolean):      {boolean_count} results")
    print(f"Words — valid_labels_array $contains:           {contains_count} results")
    print(f"Words — invalid_labels_array $contains:         {invalid_count} results")
    print(f"Lines — label_STORE_HOURS=True (boolean):       {lines_bool_count} results")
    print()
    if boolean_count == 0 and contains_count > 0:
        print("CONFIRMED: Boolean where-clause returns 0 words, $contains works.")
        print("FIX: Update _query_label_evidence_for_collection() in chroma_helpers.py")
        print("     to use $contains on valid_labels_array / invalid_labels_array")
        print("     for the words collection instead of label_STORE_HOURS=True/False.")
        print()
        print("     The utility build_label_membership_clause() in")
        print("     receipt_agent/utils/label_metadata.py already implements this.")
    elif boolean_count > 0:
        print("UNEXPECTED: Boolean query returned results on words collection.")
        print("Hypothesis may be wrong — investigate further.")
    else:
        print("Both queries returned 0 — collection may be empty or label not present.")


if __name__ == "__main__":
    main()
