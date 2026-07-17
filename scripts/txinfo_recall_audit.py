#!/usr/bin/env python3
"""Build a deterministic, merchant-balanced audit of recall-pass negatives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from txinfo_recall_recover import line_key
from txinfo_recall_suggest import CODELIKE_RE, DATETIME_RE, context_text, searchable


BROAD_METADATA_RE = re.compile(
    r"\b(?:cash|clerk|operator|associate|server|host|table|guest|register|reg|"
    r"station|lane|till|terminal|tid|pos|transaction|txn|trans|order|invoice|"
    r"ticket|receipt|reference|ref|trace|sequence|seq|rrn|stan|member|loyalty|"
    r"date|time|shift|journal|jrnl)\b",
    re.IGNORECASE,
)


def stable_rank(seed: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{value}".encode()).hexdigest()


def risk_bucket(item: dict[str, Any]) -> str:
    text = searchable(str(item.get("line_text", ""))).strip()
    context = searchable(context_text(item))
    if DATETIME_RE.search(text):
        return "date-or-time"
    if BROAD_METADATA_RE.search(text):
        return "metadata-keyword"
    if CODELIKE_RE.fullmatch(text) and re.search(r"\d", text):
        return "code-like"
    if BROAD_METADATA_RE.search(context):
        return "metadata-context"
    return "other"


def proportional_quotas(groups: dict[str, list[dict[str, Any]]], target: int) -> dict[str, int]:
    nonempty = {key: values for key, values in groups.items() if values}
    total = sum(len(values) for values in nonempty.values())
    if target > total:
        raise ValueError(f"sample target {target} exceeds population {total}")
    if target < len(nonempty):
        raise ValueError(
            f"sample target {target} cannot cover all {len(nonempty)} strata"
        )

    quotas = {key: 1 for key in nonempty}
    remaining = target - len(nonempty)
    capacity_total = total - len(nonempty)
    if remaining == 0 or capacity_total == 0:
        return quotas

    exact = {
        key: remaining * (len(values) - 1) / capacity_total
        for key, values in nonempty.items()
    }
    for key, value in exact.items():
        quotas[key] += min(len(nonempty[key]) - 1, int(value))

    unassigned = target - sum(quotas.values())
    order = sorted(
        nonempty,
        key=lambda key: (exact[key] - int(exact[key]), len(nonempty[key]), key),
        reverse=True,
    )
    while unassigned:
        progressed = False
        for key in order:
            if quotas[key] >= len(nonempty[key]):
                continue
            quotas[key] += 1
            unassigned -= 1
            progressed = True
            if not unassigned:
                break
        if not progressed:
            raise AssertionError("unable to allocate requested sample")
    return quotas


def merchant_balanced_take(
    items: list[dict[str, Any]], target: int, seed: str
) -> list[dict[str, Any]]:
    by_merchant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        merchant = str(item.get("merchant") or "(unknown)").strip().lower()
        by_merchant[merchant].append(item)
    for merchant, values in by_merchant.items():
        values.sort(key=lambda item: stable_rank(seed, f"{merchant}|{item['id']}"))

    merchants = sorted(by_merchant, key=lambda value: stable_rank(seed, value))
    selected: list[dict[str, Any]] = []
    while len(selected) < target:
        progressed = False
        for merchant in merchants:
            values = by_merchant[merchant]
            if not values:
                continue
            selected.append(values.pop(0))
            progressed = True
            if len(selected) == target:
                break
        if not progressed:
            raise AssertionError("merchant-balanced sampler exhausted early")
    return selected


def stratified_sample(
    population: list[dict[str, Any]],
    target: int,
    seed: str,
    stratum: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in population:
        groups[stratum(item)].append(item)
    quotas = proportional_quotas(groups, target)
    selected = []
    for key in sorted(groups):
        picked = merchant_balanced_take(groups[key], quotas[key], f"{seed}|{key}")
        selected.extend({**item, "audit_stratum": key} for item in picked)
    return sorted(selected, key=lambda item: stable_rank(seed, item["id"]))


def canonicalize(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": line_key(item).canonical, **item}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--kept", type=Path, required=True)
    parser.add_argument("--prefiltered", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", default="txinfo-recall-audit-2026-07-17-v1")
    parser.add_argument("--unverified-size", type=int, default=180)
    parser.add_argument("--refuted-size", type=int, default=80)
    parser.add_argument("--prefiltered-size", type=int, default=60)
    args = parser.parse_args()

    full = [canonicalize(item) for item in json.loads(args.candidates.read_text())]
    kept = [canonicalize(item) for item in json.loads(args.kept.read_text())]
    dropped_minimal = [
        canonicalize(item) for item in json.loads(args.prefiltered.read_text())
    ]
    verification = json.loads(
        (args.verification / "VERIFICATION_RESULTS.json").read_text()
    )

    full_by_id = {item["id"]: item for item in full}
    kept_by_id = {item["id"]: item for item in kept}
    dropped_by_id = {item["id"]: item for item in dropped_minimal}
    verification_by_id = {item["id"]: item for item in verification}
    for name, values, mapping in (
        ("full", full, full_by_id),
        ("kept", kept, kept_by_id),
        ("prefiltered", dropped_minimal, dropped_by_id),
        ("verification", verification, verification_by_id),
    ):
        if len(values) != len(mapping):
            raise ValueError(f"{name} contains duplicate canonical IDs")

    if set(kept_by_id) & set(dropped_by_id):
        raise ValueError("kept and prefiltered populations overlap")
    if set(kept_by_id) | set(dropped_by_id) != set(full_by_id):
        raise ValueError("kept + prefiltered populations do not reconstruct candidates")
    if not set(verification_by_id) <= set(kept_by_id):
        raise ValueError("verification includes IDs outside the kept population")

    unverified = [
        item for canonical, item in kept_by_id.items() if canonical not in verification_by_id
    ]
    refuted = [item for item in verification if item["outcome"] == "REFUTED"]
    dropped = [
        {**full_by_id[canonical], "drop_rule": minimal["drop_rule"]}
        for canonical, minimal in dropped_by_id.items()
    ]

    samples = []
    for population_name, selected in (
        (
            "unverified-negative",
            stratified_sample(
                unverified,
                args.unverified_size,
                f"{args.seed}|unverified",
                lambda item: f"{item['current_label']}|{risk_bucket(item)}",
            ),
        ),
        (
            "refuted-negative",
            stratified_sample(
                refuted,
                args.refuted_size,
                f"{args.seed}|refuted",
                lambda item: (
                    f"{item['current_label']}|"
                    f"{item['lens_content']['reason'].split(':', 1)[0]}"
                ),
            ),
        ),
        (
            "prefiltered-negative",
            stratified_sample(
                dropped,
                args.prefiltered_size,
                f"{args.seed}|prefiltered",
                lambda item: str(item["drop_rule"]),
            ),
        ),
    ):
        for item in selected:
            samples.append(
                {
                    "audit_population": population_name,
                    "audit_stratum": item.pop("audit_stratum"),
                    "expected_is_txinfo": False,
                    "review_is_txinfo": None,
                    "review_reason": "",
                    **item,
                }
            )

    sample_ids = [item["id"] for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("audit sample contains duplicate canonical IDs")

    population_counts = {
        "unverified-negative": len(unverified),
        "refuted-negative": len(refuted),
        "prefiltered-negative": len(dropped),
    }
    sample_counts = Counter(item["audit_population"] for item in samples)
    summary = {
        "seed": args.seed,
        "candidate_lines": len(full),
        "kept_lines": len(kept),
        "prefiltered_lines": len(dropped),
        "verified_lines": len(verification),
        "population_counts": population_counts,
        "sample_counts": dict(sorted(sample_counts.items())),
        "sample_size": len(samples),
        "sample_rate_by_population": {
            name: sample_counts[name] / count
            for name, count in population_counts.items()
        },
        "sample_strata": dict(
            sorted(
                Counter(
                    f"{item['audit_population']}|{item['audit_stratum']}"
                    for item in samples
                ).items()
            )
        ),
        "integrity": {
            "canonical_ids_unique": True,
            "kept_prefiltered_disjoint": True,
            "kept_plus_prefiltered_equals_candidates": True,
            "verification_subset_of_kept": True,
        },
        "method": "deterministic-stratified-merchant-balanced-negative-audit-v1",
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "AUDIT_SAMPLE.json").write_text(
        json.dumps(samples, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "AUDIT_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
