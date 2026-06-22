#!/usr/bin/env python3
"""Run an end-to-end receipt letter font/style embedding pilot.

The pilot uses OCR letter boxes to crop individual glyphs, embeds each crop as
a visual style vector, stores those vectors in a separate local Chroma DB, then
clusters and summarizes the inferred styles by receipt section.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from statistics import mean, median
from typing import Any

import boto3
from botocore.exceptions import ClientError
from PIL import Image as PILImage

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "receipt_upload"))
sys.path.insert(0, str(REPO_ROOT / "receipt_chroma"))

from receipt_chroma import ChromaClient  # noqa: E402
from receipt_dynamo import DynamoClient  # noqa: E402
from receipt_upload.font_letter_analysis import (  # noqa: E402
    LetterFontAnalysis,
    LetterImageSample,
    LineFontAnalysis,
    LineFontSample,
    build_letter_image_samples,
    build_line_font_samples,
    cluster_letter_styles,
    cluster_line_font_styles,
)

PROD_TABLE = "ReceiptsTable-d7ff76a"
COLLECTION_NAME = "letter_font_glyphs"

PRICE_RE = re.compile(r"(?<!\d)(?:\$\s*)?\d{1,4}[.,]\d{2}(?!\d)")
TOTAL_RE = re.compile(
    r"\b("
    r"TOTAL|SUBTOTAL|SUB\s*TOTAL|TAX|BALANCE|AMOUNT|CHANGE|CASH|CARD|"
    r"CREDIT|DEBIT|VISA|MASTERCARD|PAYMENT|TEND|DUE"
    r")\b",
    re.IGNORECASE,
)
HEADER_RE = re.compile(
    r"\b("
    r"STORE|MARKET|PHARMACY|WALGREENS|WALMART|TARGET|COSTCO|SAFEWAY|"
    r"TRADER|KROGER|CVS|RITE|FOODS|MART|RECEIPT"
    r")\b",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed OCR letter crops into Chroma and cluster styles."
    )
    parser.add_argument("--table", default=PROD_TABLE)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--candidate-multiplier", type=int, default=4)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--persist-dir",
        default="/tmp/receipt-letter-font-chroma-pilot",
        help="Separate local Chroma DB directory for letter glyph vectors.",
    )
    parser.add_argument("--collection-name", default=COLLECTION_NAME)
    parser.add_argument("--eps", type=float, default=0.55)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--line-eps", type=float, default=0.58)
    parser.add_argument(
        "--line-eps-sweep",
        default="0.58,0.70,0.85,1.00",
        help="Comma-separated line DBSCAN eps values to evaluate in memory.",
    )
    parser.add_argument("--line-min-samples", type=int, default=2)
    parser.add_argument("--min-letters-per-line", type=int, default=3)
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--query-check-limit", type=int, default=200)
    parser.add_argument(
        "--report-md",
        default=str(
            REPO_ROOT / "docs" / "receipt-letter-font-chroma-pilot.md"
        ),
    )
    parser.add_argument(
        "--report-json",
        default=str(
            REPO_ROOT / "docs" / "receipt-letter-font-chroma-pilot.json"
        ),
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not remove the Chroma persist directory before running.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    persist_dir = Path(args.persist_dir)
    if persist_dir.exists() and not args.no_reset:
        shutil.rmtree(persist_dir)
    persist_dir.mkdir(parents=True, exist_ok=True)

    s3_client = boto3.client("s3")
    dynamo = DynamoClient(table_name=args.table, region=args.region)
    receipts = _list_all(dynamo.list_receipts)
    receipts = sorted(
        receipts,
        key=lambda receipt: str(receipt.timestamp_added),
        reverse=True,
    )

    selected = []
    raw_ok_checked = 0
    for receipt in receipts:
        if not receipt.raw_s3_bucket or not receipt.raw_s3_key:
            continue
        if not _s3_exists(
            s3_client, receipt.raw_s3_bucket, receipt.raw_s3_key
        ):
            continue
        raw_ok_checked += 1
        selected.append(receipt)
        if raw_ok_checked >= args.limit * args.candidate_multiplier:
            break

    all_samples: list[LetterImageSample] = []
    all_lines: list[Any] = []
    all_words: list[Any] = []
    extra_metadata: dict[str, dict[str, object]] = {}
    receipt_summaries = []
    section_by_sample: dict[str, str] = {}
    section_by_line_key: dict[tuple[str | None, int | None, int], str] = {}
    errors = []

    for receipt in selected:
        if len(receipt_summaries) >= args.limit:
            break
        try:
            details = dynamo.get_image_details(receipt.image_id)
            lines = [
                line
                for line in details.receipt_lines
                if line.receipt_id == receipt.receipt_id
            ]
            words = [
                word
                for word in details.receipt_words
                if word.receipt_id == receipt.receipt_id
            ]
            letters = [
                letter
                for letter in details.receipt_letters
                if letter.receipt_id == receipt.receipt_id
            ]
            if len(lines) < 5 or len(words) < 20 or len(letters) < 100:
                continue

            image = _load_image(
                s3_client, receipt.raw_s3_bucket, receipt.raw_s3_key
            )
            section_by_line, ordered_lines, line_text_by_id = _classify_lines(
                lines, words
            )
            all_lines.extend(lines)
            all_words.extend(words)
            for line in lines:
                section_by_line_key[_line_key(line)] = section_by_line.get(
                    line.line_id, "unknown"
                )
            samples = build_letter_image_samples(
                letters,
                raw_image=image,
                min_confidence=args.min_confidence,
            )
            if len(samples) < 100:
                continue

            for sample in samples:
                section = section_by_line.get(sample.line_id, "unknown")
                section_by_sample[sample.sample_id] = section
                extra_metadata[sample.sample_id] = {
                    "section": section,
                    "source": "receipt_raw_s3",
                }

            all_samples.extend(samples)
            receipt_summaries.append(
                {
                    "image_id": receipt.image_id,
                    "receipt_id": receipt.receipt_id,
                    "date": str(receipt.timestamp_added)[:10],
                    "raw_s3_bucket": receipt.raw_s3_bucket,
                    "raw_s3_key": receipt.raw_s3_key,
                    "image_size": list(image.size),
                    "line_count": len(lines),
                    "word_count": len(words),
                    "letter_count": len(letters),
                    "embedded_letter_count": len(samples),
                    "top_lines": [
                        line_text_by_id[line.line_id]
                        for line in ordered_lines[:4]
                    ],
                    "bottom_lines": [
                        line_text_by_id[line.line_id]
                        for line in ordered_lines[-4:]
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "image_id": receipt.image_id,
                    "receipt_id": receipt.receipt_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                }
            )

    analysis = cluster_letter_styles(
        all_samples,
        eps=args.eps,
        min_samples=args.min_samples,
    )
    line_samples = build_line_font_samples(
        analysis.samples,
        lines=all_lines,
        words=all_words,
        letter_assignments=analysis.assignments,
        section_by_line=section_by_line_key,
        min_letters_per_line=args.min_letters_per_line,
    )
    line_eps_values = _line_eps_values(args.line_eps, args.line_eps_sweep)
    line_analyses_by_eps = {
        eps: cluster_line_font_styles(
            line_samples,
            eps=eps,
            min_samples=args.line_min_samples,
        )
        for eps in line_eps_values
    }
    line_analysis = line_analyses_by_eps[args.line_eps]
    line_eps_sweep = [
        _line_eps_result(eps, analysis)
        for eps, analysis in line_analyses_by_eps.items()
    ]
    cluster_by_id = {
        cluster.cluster_id: cluster for cluster in analysis.clusters
    }
    for sample_id, cluster_id in analysis.assignments.items():
        if cluster_id > 0:
            extra_metadata.setdefault(sample_id, {})[
                "style_cluster_id"
            ] = cluster_id
            extra_metadata[sample_id]["style_cluster_label"] = cluster_by_id[
                cluster_id
            ].label

    chroma_count, neighbor_quality = _persist_and_evaluate_chroma(
        samples=analysis.samples,
        assignments=analysis.assignments,
        persist_directory=str(persist_dir),
        collection_name=args.collection_name,
        extra_metadata_by_id=extra_metadata,
        limit=args.query_check_limit,
        reset_collection=not args.no_reset,
    )

    report = _build_report(
        args=args,
        persist_dir=persist_dir,
        chroma_count=chroma_count,
        receipts_seen=len(receipts),
        raw_ok_candidates=len(selected),
        receipt_summaries=receipt_summaries,
        analysis=analysis,
        line_analysis=line_analysis,
        line_eps_sweep=line_eps_sweep,
        section_by_sample=section_by_sample,
        neighbor_quality=neighbor_quality,
        errors=errors,
    )

    report_json = Path(args.report_json)
    report_md = Path(args.report_md)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True))
    report_md.write_text(_render_markdown(report))

    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(f"wrote_json={report_json}")
    print(f"wrote_markdown={report_md}")
    print(f"chroma_persist_dir={persist_dir}")


def _list_all(method: Any) -> list[Any]:
    results = []
    last_evaluated_key = None
    while True:
        batch, last_evaluated_key = method(
            last_evaluated_key=last_evaluated_key
        )
        results.extend(batch)
        if not last_evaluated_key:
            return results


def _s3_exists(s3_client: Any, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def _load_image(s3_client: Any, bucket: str, key: str) -> PILImage.Image:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return PILImage.open(BytesIO(response["Body"].read())).convert("RGB")


def _classify_lines(
    lines: list[Any],
    words: list[Any],
) -> tuple[dict[int, str], list[Any], dict[int, str]]:
    words_by_line: dict[int, list[Any]] = defaultdict(list)
    for word in words:
        words_by_line[word.line_id].append(word)

    ordered = sorted(
        lines,
        key=lambda line: (
            _bbox(line).get("y", 0.0) + _bbox(line).get("height", 0.0),
            -line.line_id,
        ),
        reverse=True,
    )
    line_text_by_id = {
        line.line_id: _line_text(line, words_by_line) for line in lines
    }
    total_lines = len(ordered)
    top_cut = max(3, round(total_lines * 0.16)) if total_lines else 0
    bottom_cut = max(3, round(total_lines * 0.18)) if total_lines else 0

    mapping = {}
    for rank, line in enumerate(ordered):
        text = line_text_by_id[line.line_id]
        if TOTAL_RE.search(text):
            section = "totals_payments"
        elif rank < top_cut or (
            rank < max(6, top_cut + 2) and HEADER_RE.search(text)
        ):
            section = "header"
        elif rank >= total_lines - bottom_cut:
            section = "footer"
        elif PRICE_RE.search(text):
            section = "line_items"
        else:
            section = "body_other"
        mapping[line.line_id] = section

    return mapping, ordered, line_text_by_id


def _line_text(line: Any, words_by_line: dict[int, list[Any]]) -> str:
    text = (getattr(line, "text", "") or "").strip()
    if text:
        return text
    return " ".join(
        getattr(word, "text", "")
        for word in sorted(
            words_by_line.get(line.line_id, []),
            key=lambda word: word.word_id,
        )
    ).strip()


def _bbox(value: Any) -> dict[str, float]:
    return getattr(value, "bounding_box", None) or {}


def _line_key(value: Any) -> tuple[str | None, int | None, int]:
    return (
        getattr(value, "image_id", None),
        getattr(value, "receipt_id", None),
        getattr(value, "line_id", -1),
    )


def _line_eps_values(selected_eps: float, sweep: str) -> list[float]:
    values = {round(selected_eps, 6)}
    for raw_value in sweep.split(","):
        raw_value = raw_value.strip()
        if not raw_value:
            continue
        values.add(round(float(raw_value), 6))
    return sorted(values)


def _line_eps_result(eps: float, analysis: LineFontAnalysis) -> dict[str, Any]:
    assigned = [
        sample
        for sample in analysis.samples
        if analysis.cluster_for_sample(sample.sample_id) is not None
    ]
    cluster_counts_by_receipt = _line_cluster_counts_by_receipt(analysis)
    return {
        "eps": eps,
        "clusters": len(analysis.clusters),
        "assigned_lines": len(assigned),
        "noise_lines": len(analysis.noise_sample_ids),
        "noise_rate": (
            len(analysis.noise_sample_ids) / len(analysis.samples)
            if analysis.samples
            else 0.0
        ),
        "section_purity": _line_cluster_section_purity(analysis),
        "median_clusters_per_receipt": (
            median(cluster_counts_by_receipt)
            if cluster_counts_by_receipt
            else 0
        ),
    }


def _persist_and_evaluate_chroma(
    *,
    samples: Sequence[LetterImageSample],
    assignments: dict[str, int],
    persist_directory: str,
    collection_name: str,
    extra_metadata_by_id: dict[str, dict[str, object]],
    limit: int,
    batch_size: int = 1000,
    reset_collection: bool = False,
) -> tuple[int, dict[str, Any]]:
    with ChromaClient(
        persist_directory=persist_directory,
        mode="write",
        metadata_only=True,
    ) as client:
        collection = client.get_collection(
            collection_name,
            create_if_missing=True,
            metadata={
                "description": "Receipt OCR letter crop style embeddings"
            },
        )
        if reset_collection and collection.count():
            existing = collection.get(include=["metadatas"])
            existing_ids = list(existing.get("ids") or [])
            for start in range(0, len(existing_ids), batch_size):
                collection.delete(ids=existing_ids[start : start + batch_size])

        for start in range(0, len(samples), batch_size):
            batch = samples[start : start + batch_size]
            client.upsert(
                collection_name=collection_name,
                ids=[sample.sample_id for sample in batch],
                embeddings=[list(sample.style_vector) for sample in batch],
                documents=[sample.text for sample in batch],
                metadatas=[
                    {
                        **_sample_metadata(sample),
                        **dict(extra_metadata_by_id.get(sample.sample_id, {})),
                    }
                    for sample in batch
                ],
            )

        chroma_count = client.count(collection_name)
        neighbor_quality = _evaluate_chroma_neighbors(
            client=client,
            samples=samples,
            assignments=assignments,
            collection_name=collection_name,
            limit=limit,
        )
        return chroma_count, neighbor_quality


def _evaluate_chroma_neighbors(
    *,
    client: ChromaClient,
    samples: Sequence[LetterImageSample],
    assignments: dict[str, int],
    collection_name: str,
    limit: int,
) -> dict[str, Any]:
    checked = 0
    top1_same_cluster = 0
    top3_any_same_cluster = 0
    same_char_neighbor_found = 0
    examples = []

    for sample in samples:
        cluster_id = assignments.get(sample.sample_id, -1)
        if cluster_id <= 0:
            continue
        result = client.query(
            collection_name=collection_name,
            query_embeddings=[list(sample.style_vector)],
            n_results=6,
            where={"normalized_char": sample.normalized_char},
            include=["metadatas", "documents", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        neighbors = [
            (neighbor_id, metadata)
            for neighbor_id, metadata in zip(ids, metadatas)
            if neighbor_id != sample.sample_id
        ]
        if not neighbors:
            continue

        checked += 1
        same_char_neighbor_found += 1
        top_ids = [
            int(metadata.get("style_cluster_id", -1))
            for _, metadata in neighbors[:3]
        ]
        if top_ids and top_ids[0] == cluster_id:
            top1_same_cluster += 1
        if cluster_id in top_ids:
            top3_any_same_cluster += 1
        if len(examples) < 5:
            examples.append(
                {
                    "query_id": sample.sample_id,
                    "query_char": sample.text,
                    "query_cluster": cluster_id,
                    "neighbors": [
                        {
                            "id": neighbor_id,
                            "cluster": metadata.get("style_cluster_id"),
                            "label": metadata.get("style_cluster_label"),
                            "section": metadata.get("section"),
                        }
                        for neighbor_id, metadata in neighbors[:3]
                    ],
                }
            )
        if checked >= limit:
            break

    return {
        "checked": checked,
        "same_char_neighbor_found": same_char_neighbor_found,
        "top1_same_cluster_rate": (
            top1_same_cluster / checked if checked else 0.0
        ),
        "top3_any_same_cluster_rate": (
            top3_any_same_cluster / checked if checked else 0.0
        ),
        "examples": examples,
    }


def _build_report(
    *,
    args: argparse.Namespace,
    persist_dir: Path,
    chroma_count: int,
    receipts_seen: int,
    raw_ok_candidates: int,
    receipt_summaries: list[dict[str, Any]],
    analysis: LetterFontAnalysis,
    line_analysis: LineFontAnalysis,
    line_eps_sweep: list[dict[str, Any]],
    section_by_sample: dict[str, str],
    neighbor_quality: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    assigned_samples = [
        sample
        for sample in analysis.samples
        if analysis.cluster_for_sample(sample.sample_id) is not None
    ]
    clusters_by_id = {
        cluster.cluster_id: cluster for cluster in analysis.clusters
    }
    section_counts: Counter[str] = Counter()
    section_cluster_labels: dict[str, Counter[str]] = defaultdict(Counter)
    section_metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    char_counts = Counter(
        sample.normalized_char for sample in analysis.samples
    )

    for sample in assigned_samples:
        cluster_id = analysis.cluster_for_sample(sample.sample_id)
        if cluster_id is None:
            continue
        section = section_by_sample.get(sample.sample_id, "unknown")
        cluster = clusters_by_id[cluster_id]
        section_counts[section] += 1
        section_cluster_labels[section][cluster.label] += 1
        for metric in (
            "box_height",
            "ink_ratio",
            "edge_density",
            "box_aspect",
        ):
            section_metrics[section][metric].append(sample.metrics[metric])

    cluster_summaries = _cluster_summaries(
        analysis,
        section_by_sample=section_by_sample,
        limit=20,
    )
    line_cluster_summaries = _line_cluster_summaries(line_analysis, limit=20)
    line_section_summaries = _line_section_summaries(line_analysis)
    assigned_line_samples = [
        sample
        for sample in line_analysis.samples
        if line_analysis.cluster_for_sample(sample.sample_id) is not None
    ]
    section_summaries = []
    for section, count in section_counts.most_common():
        section_summaries.append(
            {
                "section": section,
                "assigned_samples": count,
                "avg_box_height": _avg(section_metrics[section]["box_height"]),
                "avg_ink_ratio": _avg(section_metrics[section]["ink_ratio"]),
                "avg_edge_density": _avg(
                    section_metrics[section]["edge_density"]
                ),
                "avg_box_aspect": _avg(section_metrics[section]["box_aspect"]),
                "top_cluster_labels": dict(
                    section_cluster_labels[section].most_common(8)
                ),
            }
        )

    sample_counts = [
        item["embedded_letter_count"] for item in receipt_summaries
    ]
    cluster_counts_by_receipt = _cluster_counts_by_receipt(analysis)
    line_cluster_counts_by_receipt = _line_cluster_counts_by_receipt(
        line_analysis
    )

    summary = {
        "table": args.table,
        "receipt_limit": args.limit,
        "receipts_seen": receipts_seen,
        "raw_ok_candidates": raw_ok_candidates,
        "analysed_receipts": len(receipt_summaries),
        "embedded_letters": len(analysis.samples),
        "chroma_count": chroma_count,
        "style_clusters": len(analysis.clusters),
        "noise_letters": len(analysis.noise_sample_ids),
        "noise_rate": (
            len(analysis.noise_sample_ids) / len(analysis.samples)
            if analysis.samples
            else 0.0
        ),
        "assigned_letters": len(assigned_samples),
        "embedded_lines": len(line_analysis.samples),
        "line_style_clusters": len(line_analysis.clusters),
        "noise_lines": len(line_analysis.noise_sample_ids),
        "noise_line_rate": (
            len(line_analysis.noise_sample_ids) / len(line_analysis.samples)
            if line_analysis.samples
            else 0.0
        ),
        "assigned_lines": len(assigned_line_samples),
        "median_line_clusters_per_receipt": (
            median(line_cluster_counts_by_receipt)
            if line_cluster_counts_by_receipt
            else 0
        ),
        "letter_cluster_section_purity": _letter_cluster_section_purity(
            analysis,
            section_by_sample,
        ),
        "line_cluster_section_purity": _line_cluster_section_purity(
            line_analysis
        ),
        "median_letters_per_receipt": (
            median(sample_counts) if sample_counts else 0
        ),
        "median_clusters_per_receipt": (
            median(cluster_counts_by_receipt)
            if cluster_counts_by_receipt
            else 0
        ),
        "common_ocr_chars": dict(char_counts.most_common(15)),
        "neighbor_quality": neighbor_quality,
        "errors": len(errors),
        "persist_dir": str(persist_dir),
        "collection_name": args.collection_name,
        "eps": args.eps,
        "min_samples": args.min_samples,
        "line_eps": args.line_eps,
        "line_min_samples": args.line_min_samples,
        "min_letters_per_line": args.min_letters_per_line,
    }
    if analysis.samples:
        summary["metrics_per_letter"] = len(analysis.samples[0].metrics)
        summary["raw_vector_dim"] = len(analysis.samples[0].vector)
        summary["style_vector_dim"] = len(analysis.samples[0].style_vector)
    if line_analysis.samples:
        summary["metrics_per_line"] = len(line_analysis.samples[0].metrics)
        summary["line_vector_dim"] = len(line_analysis.samples[0].vector)

    return {
        "summary": summary,
        "section_summaries": section_summaries,
        "cluster_summaries": cluster_summaries,
        "line_section_summaries": line_section_summaries,
        "line_cluster_summaries": line_cluster_summaries,
        "line_eps_sweep": line_eps_sweep,
        "receipt_summaries": receipt_summaries,
        "errors": errors,
    }


def _cluster_summaries(
    analysis: LetterFontAnalysis,
    *,
    section_by_sample: dict[str, str],
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for cluster in sorted(
        analysis.clusters,
        key=lambda item: item.sample_count,
        reverse=True,
    )[:limit]:
        sections = Counter(
            section_by_sample.get(sample_id, "unknown")
            for sample_id in cluster.sample_ids
        )
        rows.append(
            {
                "cluster_id": cluster.cluster_id,
                "label": cluster.label,
                "sample_count": cluster.sample_count,
                "normalized_chars": list(cluster.normalized_chars[:12]),
                "text_examples": list(cluster.text_examples),
                "sections": dict(sections.most_common()),
                "avg_box_height": round(
                    cluster.metrics.get("box_height", 0), 5
                ),
                "avg_ink_ratio": round(cluster.metrics.get("ink_ratio", 0), 5),
                "avg_edge_density": round(
                    cluster.metrics.get("edge_density", 0), 5
                ),
            }
        )
    return rows


def _cluster_counts_by_receipt(analysis: LetterFontAnalysis) -> list[int]:
    clusters_by_receipt: dict[tuple[str | None, int | None], set[int]] = (
        defaultdict(set)
    )
    for sample in analysis.samples:
        cluster_id = analysis.cluster_for_sample(sample.sample_id)
        if cluster_id is None:
            continue
        clusters_by_receipt[(sample.image_id, sample.receipt_id)].add(
            cluster_id
        )
    return [len(clusters) for clusters in clusters_by_receipt.values()]


def _line_cluster_counts_by_receipt(analysis: LineFontAnalysis) -> list[int]:
    clusters_by_receipt: dict[tuple[str | None, int | None], set[int]] = (
        defaultdict(set)
    )
    for sample in analysis.samples:
        cluster_id = analysis.cluster_for_sample(sample.sample_id)
        if cluster_id is None:
            continue
        clusters_by_receipt[(sample.image_id, sample.receipt_id)].add(
            cluster_id
        )
    return [len(clusters) for clusters in clusters_by_receipt.values()]


def _line_cluster_summaries(
    analysis: LineFontAnalysis,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    samples_by_id = {sample.sample_id: sample for sample in analysis.samples}
    for cluster in sorted(
        analysis.clusters,
        key=lambda item: item.sample_count,
        reverse=True,
    )[:limit]:
        samples = [
            samples_by_id[sample_id] for sample_id in cluster.sample_ids
        ]
        sections = Counter(sample.section or "unknown" for sample in samples)
        rows.append(
            {
                "cluster_id": cluster.cluster_id,
                "label": cluster.label,
                "line_count": cluster.sample_count,
                "sections": dict(sections.most_common()),
                "text_examples": list(cluster.text_examples[:8]),
                "avg_line_height": round(
                    cluster.metrics.get("line_box_height", 0.0), 5
                ),
                "avg_letter_height": round(
                    cluster.metrics.get("median_box_height", 0.0), 5
                ),
                "avg_ink_ratio": round(
                    cluster.metrics.get("mean_ink_ratio", 0.0), 5
                ),
                "avg_gap_to_height_ratio": round(
                    cluster.metrics.get("gap_to_height_ratio", 0.0), 5
                ),
                "avg_dominant_letter_cluster_ratio": round(
                    cluster.metrics.get("dominant_letter_cluster_ratio", 0.0),
                    5,
                ),
            }
        )
    return rows


def _line_section_summaries(
    analysis: LineFontAnalysis,
) -> list[dict[str, Any]]:
    clusters_by_id = {
        cluster.cluster_id: cluster for cluster in analysis.clusters
    }
    section_counts: Counter[str] = Counter()
    section_cluster_labels: dict[str, Counter[str]] = defaultdict(Counter)
    section_cluster_ids: dict[str, Counter[int]] = defaultdict(Counter)
    section_metrics: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for sample in analysis.samples:
        cluster_id = analysis.cluster_for_sample(sample.sample_id)
        if cluster_id is None:
            continue
        section = sample.section or "unknown"
        cluster = clusters_by_id[cluster_id]
        section_counts[section] += 1
        section_cluster_labels[section][cluster.label] += 1
        section_cluster_ids[section][cluster_id] += 1
        for metric in (
            "line_box_height",
            "median_box_height",
            "mean_ink_ratio",
            "gap_to_height_ratio",
            "dominant_letter_cluster_ratio",
        ):
            section_metrics[section][metric].append(sample.metrics[metric])

    summaries = []
    for section, count in section_counts.most_common():
        dominant_count = (
            section_cluster_ids[section].most_common(1)[0][1]
            if section_cluster_ids[section]
            else 0
        )
        summaries.append(
            {
                "section": section,
                "assigned_lines": count,
                "dominant_cluster_share": (
                    dominant_count / count if count else 0.0
                ),
                "avg_line_height": _avg(
                    section_metrics[section]["line_box_height"]
                ),
                "avg_letter_height": _avg(
                    section_metrics[section]["median_box_height"]
                ),
                "avg_ink_ratio": _avg(
                    section_metrics[section]["mean_ink_ratio"]
                ),
                "avg_gap_to_height_ratio": _avg(
                    section_metrics[section]["gap_to_height_ratio"]
                ),
                "avg_dominant_letter_cluster_ratio": _avg(
                    section_metrics[section]["dominant_letter_cluster_ratio"]
                ),
                "top_cluster_labels": dict(
                    section_cluster_labels[section].most_common(8)
                ),
                "top_cluster_ids": dict(
                    section_cluster_ids[section].most_common(8)
                ),
            }
        )
    return summaries


def _letter_cluster_section_purity(
    analysis: LetterFontAnalysis,
    section_by_sample: dict[str, str],
) -> float:
    if not analysis.clusters:
        return 0.0
    numerator = 0
    denominator = 0
    for cluster in analysis.clusters:
        sections = Counter(
            section_by_sample.get(sample_id, "unknown")
            for sample_id in cluster.sample_ids
        )
        numerator += max(sections.values()) if sections else 0
        denominator += sum(sections.values())
    return numerator / denominator if denominator else 0.0


def _line_cluster_section_purity(analysis: LineFontAnalysis) -> float:
    if not analysis.clusters:
        return 0.0
    samples_by_id = {sample.sample_id: sample for sample in analysis.samples}
    numerator = 0
    denominator = 0
    for cluster in analysis.clusters:
        sections = Counter(
            samples_by_id[sample_id].section or "unknown"
            for sample_id in cluster.sample_ids
        )
        numerator += max(sections.values()) if sections else 0
        denominator += sum(sections.values())
    return numerator / denominator if denominator else 0.0


def _sample_metadata(sample: LetterImageSample) -> dict[str, object]:
    metadata: dict[str, object] = {
        "ocr_char": sample.text,
        "normalized_char": sample.normalized_char,
        "line_id": sample.line_id,
        "word_id": sample.word_id,
        "letter_id": sample.letter_id,
        "confidence": sample.metrics.get("confidence", 0.0),
        "ink_ratio": sample.metrics.get("ink_ratio", 0.0),
        "box_height": sample.metrics.get("box_height", 0.0),
        "box_width": sample.metrics.get("box_width", 0.0),
    }
    if sample.image_id is not None:
        metadata["image_id"] = sample.image_id
    if sample.receipt_id is not None:
        metadata["receipt_id"] = sample.receipt_id
    return metadata


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Receipt Letter Font Chroma Pilot",
        "",
        "Date: 2026-06-22",
        "",
        "## Run Summary",
        "",
        f"- Dynamo table: `{summary['table']}`",
        f"- Local Chroma DB: `{summary['persist_dir']}`",
        f"- Collection: `{summary['collection_name']}`",
        f"- Analysed receipts: {summary['analysed_receipts']}",
        f"- Embedded letters stored in Chroma: {summary['chroma_count']}",
        f"- Letter style clusters: {summary['style_clusters']}",
        f"- Noise letters: {summary['noise_letters']} "
        f"({summary['noise_rate']:.1%})",
        f"- Median letters per receipt: {summary['median_letters_per_receipt']}",
        f"- Median letter clusters per receipt: {summary['median_clusters_per_receipt']}",
        f"- Line signatures: {summary['embedded_lines']}",
        f"- Line style clusters: {summary['line_style_clusters']}",
        f"- Noise lines: {summary['noise_lines']} "
        f"({summary['noise_line_rate']:.1%})",
        f"- Assigned lines: {summary['assigned_lines']}",
        f"- Median line clusters per receipt: {summary['median_line_clusters_per_receipt']}",
        f"- Letter cluster section purity: "
        f"{summary['letter_cluster_section_purity']:.1%}",
        f"- Line cluster section purity: "
        f"{summary['line_cluster_section_purity']:.1%}",
        f"- Letter DBSCAN `eps`: {summary['eps']}",
        f"- Letter DBSCAN `min_samples`: {summary['min_samples']}",
        f"- Line DBSCAN `eps`: {summary['line_eps']}",
        f"- Line DBSCAN `min_samples`: {summary['line_min_samples']}",
        f"- Errors: {summary['errors']}",
        f"- Metrics per letter: {summary.get('metrics_per_letter', 0)}",
        f"- Raw vector dimension: {summary.get('raw_vector_dim', 0)}",
        f"- Style vector dimension: {summary.get('style_vector_dim', 0)}",
        f"- Metrics per line: {summary.get('metrics_per_line', 0)}",
        f"- Line vector dimension: {summary.get('line_vector_dim', 0)}",
        "",
        "## Line Threshold Sweep",
        "",
        "| Line eps | Clusters | Assigned lines | Noise | Section purity | Median clusters/receipt |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["line_eps_sweep"]:
        selected = " (selected)" if row["eps"] == summary["line_eps"] else ""
        lines.append(
            f"| {row['eps']:.2f}{selected} | {row['clusters']} | "
            f"{row['assigned_lines']} | {row['noise_rate']:.1%} | "
            f"{row['section_purity']:.1%} | "
            f"{row['median_clusters_per_receipt']} |"
        )
    lines.extend(
        [
            "",
            "## Feature Inventory",
            "",
            "Each letter embedding combines deterministic visual features before "
            "any clustering:",
            "",
            "- normalized glyph bitmap",
            "- OCR character-centered residual vector",
            "- bbox size/aspect and OCR confidence",
            "- ink density and content coverage",
            "- edge density and horizontal/vertical transition rates",
            "- row and column projection bins",
            "- HOG-style unsigned gradient orientation bins",
            "- stroke-width run estimates",
            "- top/bottom/left/right contour profile stats",
            "- horizontal and vertical symmetry",
            "- connected component count and largest component ratio",
            "- enclosed hole count and hole area ratio",
            "",
            "Each line signature then aggregates those letter features with line "
            "geometry, letter spacing, word/letter counts, OCR character mix, "
            "dominant letter-cluster mix, and averaged letter style-vector "
            "centroids/spread.",
            "",
            "## Chroma Neighbor Check",
            "",
        ]
    )
    neighbor = summary["neighbor_quality"]
    lines.extend(
        [
            f"- Same-character queries checked: {neighbor['checked']}",
            f"- Top-1 same-cluster rate: "
            f"{neighbor['top1_same_cluster_rate']:.1%}",
            f"- Top-3 any same-cluster rate: "
            f"{neighbor['top3_any_same_cluster_rate']:.1%}",
            "",
            "The neighbor check queries Chroma with a letter style vector, "
            "filtered to the same OCR character, and checks whether nearest "
            "neighbors share the discovered style cluster.",
            "",
            "## Letter Section Patterns",
            "",
            "| Section | Assigned letters | Avg height | Avg ink | Avg edge | Top style labels |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for section in report["section_summaries"]:
        labels = ", ".join(
            f"`{label}` ({count})"
            for label, count in section["top_cluster_labels"].items()
        )
        lines.append(
            f"| `{section['section']}` | {section['assigned_samples']} | "
            f"{section['avg_box_height']:.4f} | "
            f"{section['avg_ink_ratio']:.3f} | "
            f"{section['avg_edge_density']:.3f} | {labels} |"
        )
    lines.extend(
        [
            "",
            "## Line Section Patterns",
            "",
            "| Section | Assigned lines | Dominant cluster share | Avg line height | Avg letter height | Avg ink | Gap/height | Top line labels |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for section in report["line_section_summaries"]:
        labels = ", ".join(
            f"`{label}` ({count})"
            for label, count in section["top_cluster_labels"].items()
        )
        lines.append(
            f"| `{section['section']}` | {section['assigned_lines']} | "
            f"{section['dominant_cluster_share']:.1%} | "
            f"{section['avg_line_height']:.4f} | "
            f"{section['avg_letter_height']:.4f} | "
            f"{section['avg_ink_ratio']:.3f} | "
            f"{section['avg_gap_to_height_ratio']:.3f} | {labels} |"
        )
    lines.extend(
        [
            "",
            "## Largest Letter Style Clusters",
            "",
            "| Cluster | Label | Letters | Chars | Sections |",
            "|---:|---|---:|---|---|",
        ]
    )
    for cluster in report["cluster_summaries"][:12]:
        chars = " ".join(
            f"`{char}`" for char in cluster["normalized_chars"][:8]
        )
        sections = ", ".join(
            f"`{section}` ({count})"
            for section, count in cluster["sections"].items()
        )
        lines.append(
            f"| {cluster['cluster_id']} | `{cluster['label']}` | "
            f"{cluster['sample_count']} | {chars} | {sections} |"
        )
    lines.extend(
        [
            "",
            "## Largest Line Style Clusters",
            "",
            "| Cluster | Label | Lines | Sections | Examples |",
            "|---:|---|---:|---|---|",
        ]
    )
    for cluster in report["line_cluster_summaries"][:12]:
        sections = ", ".join(
            f"`{section}` ({count})"
            for section, count in cluster["sections"].items()
        )
        examples = "; ".join(
            example.replace("|", " ")
            for example in cluster["text_examples"][:3]
            if example
        )
        lines.append(
            f"| {cluster['cluster_id']} | `{cluster['label']}` | "
            f"{cluster['line_count']} | {sections} | {examples} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This end-to-end pilot proves the mechanics work: individual OCR "
            "letter crops can be embedded, persisted in a separate Chroma DB, "
            "queried by same-character nearest neighbors, clustered into "
            "visual style groups, and aggregated into line-level font "
            "signatures.",
            "",
            "Line-level signatures are the better unit for section inference "
            "because they carry stable typography and layout context. The "
            "purity scores above compare how often each discovered cluster is "
            "dominated by a single heuristic receipt section.",
            "",
            "It is not yet an exact font-family recognizer. The current output "
            "is relative font/style clustering: size, width, stroke darkness, "
            "and visual similarity. Exact font names would require a reference "
            "library of rendered fonts and a calibration step against that "
            "library.",
            "",
            "## Next Improvements",
            "",
            "- Tune `eps` per dataset or switch to HDBSCAN when available.",
            "- Prefer persisted `ReceiptSection` entities over the heuristic "
            "line classifier used in this pilot.",
            "- Add a reference-font corpus to map clusters to likely font "
            "family names.",
            "- Use line signatures as features for a supervised section model "
            "once section labels exist.",
            "",
        ]
    )
    return "\n".join(lines)


def _avg(values: list[float]) -> float:
    return round(mean(values), 5) if values else 0.0


if __name__ == "__main__":
    main()
