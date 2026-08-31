#!/usr/bin/env python3
"""Score a VectorSearchClient against golden similarity fixtures.

    python scripts/similarity_harness/evaluate.py --backend fake
    python scripts/similarity_harness/evaluate.py --backend chroma --out scorecard.json
    python scripts/similarity_harness/evaluate.py --backend dynamo

Backends:

* ``fake`` — exact cosine over ``corpus.json`` (offline, deterministic)
* ``chroma`` — live Chroma Cloud when CHROMA_CLOUD_* is set; otherwise
  replays captured neighbors (self-parity sanity, scores ≈ 1.0)
* ``dynamo`` — read-only SearchVectors. Never creates indexes. Errors
  without AWS / boto3 search_vectors.

Metrics follow SPEC §8 and AGENT_PLAN (recall@k, merchant agreement %,
tier distribution, latency percentiles, est. $/query). Given the same
fixtures, this script is pure aside from measured wall-clock latency.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.similarity_harness.backends import (  # noqa: E402
    ChromaVectorClient,
    DynamoVectorClient,
    ReplayVectorClient,
    fake_from_corpus,
)
from scripts.similarity_harness.common import (  # noqa: E402
    DEFAULT_FIXTURE_DIR,
    chroma_cloud_configured,
    dump_json,
    load_json,
    merchant_decision_from_neighbors,
    scored_to_neighbor,
    section_vote_from_neighbors,
)
from scripts.similarity_harness.metrics import (  # noqa: E402
    agreement_pct,
    distribution,
    estimate_request_units,
    max_pp_gap,
    mean,
    percentile,
    recall_at_k,
    spec_gates,
    usd_from_request_units,
)

from receipt_embeddings.vector_client import (  # noqa: E402
    VectorSearchClient,
    normalize_index_name,
)


def _load_golden(fixture_dir: Path) -> dict[str, Any]:
    path = fixture_dir / "golden.json"
    if not path.exists():
        raise SystemExit(f"missing golden fixture: {path}")
    return load_json(path)


def _load_corpus(fixture_dir: Path) -> dict[str, Any]:
    path = fixture_dir / "corpus.json"
    if not path.exists():
        raise SystemExit(
            f"missing corpus fixture {path} (required for --backend fake)"
        )
    return load_json(path)


def _iter_queries(golden: Mapping[str, Any]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    for receipt in golden["receipts"]:
        merchant = dict(receipt["merchant_resolution"])
        merchant["family"] = "merchant"
        merchant["image_id"] = receipt["image_id"]
        merchant["receipt_id"] = receipt["receipt_id"]
        queries.append(merchant)
        for word in receipt.get("word_queries") or []:
            item = dict(word)
            item["family"] = "words"
            item["image_id"] = receipt["image_id"]
            item["receipt_id"] = receipt["receipt_id"]
            queries.append(item)
        for row in (receipt.get("section_verifier") or {}).get(
            "row_queries"
        ) or []:
            item = dict(row)
            item["family"] = "sections"
            item["image_id"] = receipt["image_id"]
            item["receipt_id"] = receipt["receipt_id"]
            queries.append(item)
    return queries


def build_backend(
    name: str,
    *,
    fixture_dir: Path,
    golden: Mapping[str, Any],
) -> tuple[VectorSearchClient, str]:
    """Return (client, resolved_backend_name)."""
    if name == "fake":
        corpus = _load_corpus(fixture_dir)
        return fake_from_corpus(corpus), "fake"
    if name == "chroma":
        if chroma_cloud_configured():
            import os

            from receipt_chroma import ChromaClient

            chroma = ChromaClient(
                cloud_api_key=os.environ["CHROMA_CLOUD_API_KEY"].strip(),
                cloud_tenant=os.environ["CHROMA_CLOUD_TENANT"].strip(),
                cloud_database=os.environ["CHROMA_CLOUD_DATABASE"].strip(),
                mode="read",
            )
            return ChromaVectorClient(chroma), "chroma"
        return ReplayVectorClient(_iter_queries(golden)), "chroma_replay"
    if name == "dynamo":
        return DynamoVectorClient(), "dynamo"
    raise SystemExit(f"unknown backend {name!r}")


def _run_search(
    client: VectorSearchClient,
    query: Mapping[str, Any],
) -> dict[str, Any]:
    vector = query["query_vector"]
    index = normalize_index_name(str(query["index"]))
    top_k = int(query["top_k"])
    started = time.perf_counter()
    results = client.search(vector, index, top_k)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if hasattr(client, "last_request_units"):
        reported = getattr(client, "last_request_units")
        request_units = (
            float(reported)
            if reported is not None
            else estimate_request_units(top_k, None)
        )
    else:
        # FakeVectorIndex has no request-unit accounting; cost is $0.
        request_units = 0.0
    return {
        "neighbors": [scored_to_neighbor(item) for item in results],
        "keys": [item.key for item in results],
        "latency_ms": elapsed_ms,
        "request_units": request_units,
    }


def evaluate(
    client: VectorSearchClient,
    golden: Mapping[str, Any],
    *,
    backend: str,
) -> dict[str, Any]:
    receipts = golden["receipts"]
    merchant_pairs: list[tuple[Any, Any]] = []
    merchant_truth_pairs: list[tuple[Any, Any]] = []
    tier_pairs: list[tuple[Any, Any]] = []
    backend_tiers: list[str] = []
    golden_tiers: list[str] = []
    vote_pairs: list[tuple[Any, Any]] = []
    recalls: dict[str, list[float]] = {
        "merchant": [],
        "words": [],
        "sections": [],
        "recall@10": [],
    }
    latencies: list[float] = []
    costs: list[float] = []

    for receipt in receipts:
        merchant_q = receipt["merchant_resolution"]
        merchant_run = _run_search(client, merchant_q)
        latencies.append(merchant_run["latency_ms"])
        costs.append(usd_from_request_units(merchant_run["request_units"]))
        golden_keys = [n["key"] for n in merchant_q["neighbors"]]
        recalls["merchant"].append(
            recall_at_k(
                merchant_run["keys"], golden_keys, int(merchant_q["top_k"])
            )
        )
        recalls["recall@10"].append(
            recall_at_k(merchant_run["keys"], golden_keys, 10)
        )
        decision = merchant_decision_from_neighbors(
            merchant_run["neighbors"],
            image_id=str(receipt["image_id"]),
            receipt_id=int(receipt["receipt_id"]),
            query_kind=str(merchant_q.get("query_kind") or "text"),
        )
        merchant_pairs.append(
            (decision["decision"], merchant_q.get("decision"))
        )
        merchant_truth_pairs.append(
            (decision["decision"], receipt.get("merchant_truth"))
        )
        tier_pairs.append((decision["tier"], merchant_q.get("tier")))
        backend_tiers.append(str(decision["tier"]))
        golden_tiers.append(str(merchant_q.get("tier")))

        for word in receipt.get("word_queries") or []:
            run = _run_search(client, word)
            latencies.append(run["latency_ms"])
            costs.append(usd_from_request_units(run["request_units"]))
            golden_keys = [n["key"] for n in word["neighbors"]]
            recalls["words"].append(
                recall_at_k(run["keys"], golden_keys, int(word["top_k"]))
            )
            recalls["recall@10"].append(
                recall_at_k(run["keys"], golden_keys, 10)
            )

        for row in (receipt.get("section_verifier") or {}).get(
            "row_queries"
        ) or []:
            run = _run_search(client, row)
            latencies.append(run["latency_ms"])
            costs.append(usd_from_request_units(run["request_units"]))
            golden_keys = [n["key"] for n in row["neighbors"]]
            recalls["sections"].append(
                recall_at_k(run["keys"], golden_keys, int(row["top_k"]))
            )
            recalls["recall@10"].append(
                recall_at_k(run["keys"], golden_keys, 10)
            )
            vote = section_vote_from_neighbors(
                run["neighbors"],
                image_id=str(receipt["image_id"]),
                receipt_id=int(receipt["receipt_id"]),
                proposed_section_type=str(
                    row.get("proposed_section_type") or ""
                ),
            )
            vote_pairs.append((vote["vote"], row.get("vote")))

    metrics = {
        "neighbor_recall_at_k": {
            "merchant@20": mean(recalls["merchant"]),
            "words@30": mean(recalls["words"]),
            "sections@15": mean(recalls["sections"]),
            "recall@10": mean(recalls["recall@10"]),
            "macro": mean(
                [
                    mean(recalls["merchant"]),
                    mean(recalls["words"]),
                    mean(recalls["sections"]),
                ]
            ),
        },
        "merchant_agreement_pct": agreement_pct(merchant_pairs),
        "merchant_truth_agreement_pct": agreement_pct(merchant_truth_pairs),
        "tier_agreement_pct": agreement_pct(tier_pairs),
        "tier_distribution": {
            "backend": distribution(backend_tiers),
            "golden": distribution(golden_tiers),
        },
        "tier_distribution_pp_gap": max_pp_gap(
            distribution(backend_tiers), distribution(golden_tiers)
        ),
        "section_vote_agreement_pct": agreement_pct(vote_pairs),
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "n": len(latencies),
        },
        "est_usd_per_query": mean(costs),
        "cost_model": (
            "Fake and Chroma incremental cost is $0. Dynamo uses "
            "ConsumedCapacity.ReadRequestUnits when present, else "
            "1 + ceil(top_k * 512 / 4096) RRUs at $0.25 / million "
            "(us-east-1 on-demand, SPEC research 2026-08)."
        ),
    }
    scorecard = {
        "backend": backend,
        "n_receipts": len(receipts),
        "n_queries": len(latencies),
        "fixture_meta": golden.get("meta"),
        "metrics": metrics,
        "gates": spec_gates(metrics),
        "pure_given_fixtures": True,
        "latency_is_wall_clock": True,
    }
    scorecard["all_gates_pass"] = all(
        item["pass"] for item in scorecard["gates"]
    )
    return scorecard


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("fake", "dynamo", "chroma"),
        required=True,
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write scorecard JSON to this path (also printed)",
    )
    args = parser.parse_args(argv)

    golden = _load_golden(args.fixtures)
    client, resolved = build_backend(
        args.backend, fixture_dir=args.fixtures, golden=golden
    )
    try:
        scorecard = evaluate(client, golden, backend=resolved)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    dump_target = args.out
    if dump_target is not None:
        dump_json(dump_target, scorecard)
    print(dump_json_stdout(scorecard))
    return 0


def dump_json_stdout(payload: Mapping[str, Any]) -> str:
    import json

    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
