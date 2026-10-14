"""Offline, opt-in CPU experiment; never imported by receipt ingestion.

Pairwise logistic regression learns shape features from manual golden names.
It can only select OCR text inside an existing decoded item's block. It cannot
create items, amounts, or sections. No third-party ML runtime is required.
"""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import inspect
import json
import math
import random
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from receipt_upload.line_items.geometry import (
    extract_items,
    is_line_price_word,
)

FEATURES = [
    "alpha_fraction",
    "digit_fraction",
    "space_fraction",
    "punct_fraction",
    "word_count",
    "alpha_word_count",
    "mixed_word_fraction",
    "long_numeric_fraction",
    "max_alpha_run",
    "character_count",
    "leading_numeric",
    "baseline_candidate",
]
THRESHOLD = 0.9  # Predeclared pairwise score gate, NOT calibrated probability.


def normalize(text: str) -> str:
    """Case/punctuation insensitive; preserve numbers, units and all letters."""
    return " ".join(re.findall(r"[A-Z0-9]+", text.upper()))


def cents(value: Any) -> int | None:
    try:
        return int(
            (
                Decimal(str(value).replace("$", "").replace(",", "")) * 100
            ).quantize(Decimal("1"))
        )
    except (InvalidOperation, ValueError, TypeError):
        return None


def merchant_group(name: str) -> str:
    value = normalize(name)
    if value.startswith("WILD FORK"):
        return "WILD FORK"
    return value


def features(text: str, baseline: bool) -> list[float]:
    tokens = text.split()
    n, k = max(1, len(text)), max(1, len(tokens))
    return [
        sum(c.isalpha() for c in text) / n,
        sum(c.isdigit() for c in text) / n,
        sum(c.isspace() for c in text) / n,
        sum(not c.isalnum() and not c.isspace() for c in text) / n,
        min(len(tokens), 12) / 12,
        min(sum(t.isalpha() for t in tokens), 12) / 12,
        sum(
            any(c.isalpha() for c in t) and any(c.isdigit() for c in t)
            for t in tokens
        )
        / k,
        sum(t.isdigit() and len(t) >= 5 for t in tokens) / k,
        min(
            max((len(t) for t in re.findall(r"[A-Za-z]+", text)), default=0),
            20,
        )
        / 20,
        min(len(text), 100) / 100,
        float(bool(tokens and tokens[0].isdigit())),
        float(baseline),
    ]


def candidates(item: dict, words: list[dict]) -> list[dict]:
    """No truth access. Restrict alternatives to baseline block provenance."""
    result = [
        {
            "name": item.get("name", ""),
            "refs": item.get("name_word_ids", []),
            "baseline": True,
        }
    ]
    by_line: dict[int, list[dict]] = collections.defaultdict(list)
    allowed = set(item.get("line_ids", []))
    for word in words:
        if word["line_id"] in allowed:
            by_line[word["line_id"]].append(word)
    for _, line in sorted(by_line.items()):
        kept = [
            w
            for w in sorted(line, key=lambda w: w["x"])
            if not is_line_price_word(w)
        ]
        # Offer raw and leading-SKU-stripped variants, retaining printed units.
        variants = [kept]
        if kept and re.fullmatch(r"\d{5,}", kept[0]["text"]):
            variants.append(kept[1:])
        for variant in variants:
            name = " ".join(w["text"] for w in variant).strip()
            if not any(c.isalpha() for c in name):
                continue
            result.append(
                {
                    "name": name,
                    "baseline": False,
                    "refs": [
                        {"line_id": w["line_id"], "word_id": w["word_id"]}
                        for w in variant
                    ],
                }
            )
    unique: dict[str, dict] = {}
    for candidate in result:
        key = normalize(candidate["name"])
        if key not in unique:
            candidate["features"] = features(
                candidate["name"], candidate["baseline"]
            )
            unique[key] = candidate
    return list(unique.values())


def manual_target(item: dict, truth: list[dict]) -> dict | None:
    """Unique manual item matching price carrier + amount; not decoder labels."""
    line = (item.get("price_word_id") or {}).get("line_id")
    matches = [
        t
        for t in truth
        if not t.get("is_discount")
        and line in t.get("line_ids", [])
        and cents(t.get("price")) == cents(item.get("price"))
    ]
    return matches[0] if len(matches) == 1 else None


def training_pairs(receipts: list[dict]) -> list[list[float]]:
    pairs = []
    for receipt in receipts:
        for item, choices in zip(receipt["pred"], receipt["choices"]):
            target = manual_target(item, receipt["truth"])
            if target is None:
                continue
            positives = [
                c
                for c in choices
                if normalize(c["name"]) == normalize(target["name"])
            ]
            # Do not label all candidates negative when truth is absent in OCR.
            if not positives:
                continue
            positive = positives[0]
            for negative in choices:
                if normalize(negative["name"]) != normalize(target["name"]):
                    pairs.append(
                        [
                            a - b
                            for a, b in zip(
                                positive["features"], negative["features"]
                            )
                        ]
                    )
    return pairs


def sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, value))))


def score(weights: list[float], values: list[float]) -> float:
    return sum(w * x for w, x in zip(weights, values))


def train(pairs: list[list[float]]) -> list[float]:
    weights = [0.0] * len(FEATURES)
    if not pairs:
        return weights
    # Full-batch deterministic gradient descent, fixed hyperparameters.
    for _ in range(400):
        gradient = [0.0] * len(weights)
        for pair in pairs:
            residual = sigmoid(score(weights, pair)) - 1
            for j, x in enumerate(pair):
                gradient[j] += residual * x
        weights = [
            w - 0.5 * (g / len(pairs) + 0.02 * w)
            for w, g in zip(weights, gradient)
        ]
    return weights


def predict(
    receipt: dict, weights: list[float], threshold: float
) -> tuple[list[dict], int]:
    output, changed = [], 0
    for item, choices in zip(receipt["pred"], receipt["choices"]):
        selected = max(choices, key=lambda c: score(weights, c["features"]))
        preference = sigmoid(
            score(weights, selected["features"])
            - score(weights, choices[0]["features"])
        )
        updated = copy.deepcopy(item)
        if selected is not choices[0] and preference >= threshold:
            updated["name"] = selected["name"]
            updated["name_word_ids"] = selected["refs"]
            changed += 1
        output.append(updated)
    return output, changed


def metrics(
    truth: list[dict],
    predictions: list[dict],
    subtotal: Any,
    subtotal_predictions: list[dict] | None = None,
) -> dict:
    all_predictions = (
        predictions if subtotal_predictions is None else subtotal_predictions
    )
    truth = [t for t in truth if not t.get("is_discount")]
    predictions = [p for p in predictions if not p.get("is_discount")]

    def bag(items: list[dict], with_name: bool) -> collections.Counter:
        return collections.Counter(
            (
                (cents(i.get("price")), normalize(i.get("name", "")))
                if with_name
                else cents(i.get("price"))
            )
            for i in items
        )

    joint = sum((bag(truth, True) & bag(predictions, True)).values())
    price = sum((bag(truth, False) & bag(predictions, False)).values())
    name = sum(
        (
            collections.Counter(normalize(i.get("name", "")) for i in truth)
            & collections.Counter(
                normalize(i.get("name", "")) for i in predictions
            )
        ).values()
    )
    target = cents(subtotal)
    total = sum(cents(p.get("price")) or 0 for p in all_predictions)
    return {
        "truth": len(truth),
        "predicted": len(predictions),
        "joint_tp": joint,
        "name_tp": name,
        "price_tp": price,
        "missing_joint": len(truth) - joint,
        "spurious_joint": len(predictions) - joint,
        "missing_price": len(truth) - price,
        "spurious_price": len(predictions) - price,
        "joint_precision": joint / len(predictions) if predictions else None,
        "joint_recall": joint / len(truth) if truth else None,
        "price_precision": price / len(predictions) if predictions else None,
        "price_recall": price / len(truth) if truth else None,
        "name_precision": name / len(predictions) if predictions else None,
        "name_recall": name / len(truth) if truth else None,
        "subtotal_delta_cents": total - target if target is not None else None,
        "subtotal_exact": total == target if target is not None else None,
    }


def aggregate(rows: list[dict]) -> dict:
    keys = [
        "truth",
        "predicted",
        "joint_tp",
        "name_tp",
        "price_tp",
        "missing_joint",
        "spurious_joint",
        "missing_price",
        "spurious_price",
    ]
    result = {k: sum(r[k] for r in rows) for k in keys}
    for prefix in ("joint", "price", "name"):
        for metric, denominator in (
            ("precision", "predicted"),
            ("recall", "truth"),
        ):
            result[f"{prefix}_{metric}"] = (
                result[f"{prefix}_tp"] / result[denominator]
                if result[denominator]
                else None
            )
    result["subtotal_evaluable"] = sum(
        r["subtotal_exact"] is not None for r in rows
    )
    result["subtotal_exact"] = sum(r["subtotal_exact"] is True for r in rows)
    return result


def run(fixtures: Path, output: Path, exclude_uncertain: bool = False) -> dict:
    golden_path = fixtures / "line_items_golden.json"
    ocr_path = fixtures / "line_items_golden_ocr.json"
    golden = json.loads(golden_path.read_text())["receipts"]
    ocr = {
        (r["image_id"], r["receipt_id"]): r
        for r in json.loads(ocr_path.read_text())["receipts"]
    }
    receipts = []
    excluded_truth = excluded_predictions = 0
    for gold in golden:
        source = ocr[(gold["image_id"], gold["receipt_id"])]
        pred, _ = extract_items(source["words"], set(source["items_line_ids"]))
        all_predictions = pred
        truth = gold["true_items"]
        if exclude_uncertain:
            uncertain = [t for t in truth if t.get("uncertain")]
            ignored_lines = {
                line for t in uncertain for line in t.get("line_ids", [])
            }
            certain_lines = {
                line
                for t in truth
                if not t.get("uncertain")
                for line in t.get("line_ids", [])
            }
            if any(
                (p.get("price_word_id") or {}).get("line_id")
                in ignored_lines & certain_lines
                for p in pred
            ):
                raise ValueError(
                    "Uncertain exclusion overlaps a retained target carrier"
                )
            kept = [
                p
                for p in pred
                if (p.get("price_word_id") or {}).get("line_id")
                not in ignored_lines
            ]
            excluded_truth += len(uncertain)
            excluded_predictions += len(pred) - len(kept)
            pred = kept
            truth = [t for t in truth if not t.get("uncertain")]
        receipts.append(
            {
                "id": f'{gold["image_id"]}/{gold["receipt_id"]}',
                "merchant": merchant_group(gold["merchant"]),
                "truth": truth,
                "pred": pred,
                "all_predictions": all_predictions,
                "choices": [candidates(p, source["words"]) for p in pred],
                "subtotal": gold.get("printed_subtotal"),
            }
        )
    folds, results = [], []
    for merchant in sorted({r["merchant"] for r in receipts}):
        training = [r for r in receipts if r["merchant"] != merchant]
        heldout = [r for r in receipts if r["merchant"] == merchant]
        pairs = training_pairs(training)
        weights = train(pairs)
        folds.append(
            {
                "merchant": merchant,
                "split_sha256": hashlib.sha256(
                    json.dumps(
                        {
                            "train": sorted(r["id"] for r in training),
                            "test": sorted(r["id"] for r in heldout),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "training_receipts": len(training),
                "heldout_receipts": len(heldout),
                "training_pairs": len(pairs),
            }
        )
        for receipt in heldout:
            gated, changed = predict(receipt, weights, THRESHOLD)
            ungated, ungated_changed = predict(receipt, weights, 0.5)
            results.append(
                {
                    "id": receipt["id"],
                    "merchant": merchant,
                    "baseline": metrics(
                        receipt["truth"],
                        receipt["pred"],
                        receipt["subtotal"],
                        receipt["all_predictions"],
                    ),
                    "gated": metrics(
                        receipt["truth"],
                        gated,
                        receipt["subtotal"],
                        receipt["all_predictions"],
                    ),
                    "ungated": metrics(
                        receipt["truth"],
                        ungated,
                        receipt["subtotal"],
                        receipt["all_predictions"],
                    ),
                    "changed": changed,
                    "ungated_changed": ungated_changed,
                    "ungated_changes": [
                        {
                            "from": a["name"],
                            "to": b["name"],
                            "price": a["price"],
                            "manual_target": (
                                manual_target(a, receipt["truth"]) or {}
                            ).get("name"),
                        }
                        for a, b in zip(receipt["pred"], ungated)
                        if a["name"] != b["name"]
                    ],
                    "changes": [
                        {
                            "from": a["name"],
                            "to": b["name"],
                            "price": a["price"],
                        }
                        for a, b in zip(receipt["pred"], gated)
                        if a["name"] != b["name"]
                    ],
                }
            )
    per_merchant = []
    for fold in folds:
        rows = [r for r in results if r["merchant"] == fold["merchant"]]
        per_merchant.append(
            {
                **fold,
                **{
                    kind: aggregate([r[kind] for r in rows])
                    for kind in ("baseline", "gated", "ungated")
                },
            }
        )
    pairs = training_pairs(receipts)
    summary = {
        kind: aggregate([r[kind] for r in results])
        for kind in ("baseline", "gated", "ungated")
    }
    total = summary["baseline"]["predicted"]
    changed = sum(r["changed"] for r in results)
    rng = random.Random(20260930)
    deltas = []
    for _ in range(2000):
        sample = rng.choices(per_merchant, k=len(per_merchant))
        denominator = sum(r["baseline"]["truth"] for r in sample)
        if denominator:
            deltas.append(
                sum(
                    r["ungated"]["joint_tp"] - r["baseline"]["joint_tp"]
                    for r in sample
                )
                / denominator
            )
    deltas.sort()
    decoder_dir = Path(inspect.getfile(extract_items)).parent
    report = {
        "exclude_uncertain": exclude_uncertain,
        "excluded_uncertain_truth": excluded_truth,
        "excluded_uncertain_predictions": excluded_predictions,
        "experiment_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "hyperparameters": {
            "epochs": 400,
            "learning_rate": 0.5,
            "l2": 0.02,
            "gated_threshold": THRESHOLD,
            "diagnostic_threshold": 0.5,
            "bootstrap_seed": 20260930,
            "bootstrap_samples": 2000,
        },
        "decoder_source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (decoder_dir / "geometry.py", decoder_dir / "blocks.py")
        },
        "ungated_cluster_bootstrap_joint_recall_delta_95pct": [
            deltas[int(len(deltas) * q)] for q in (0.025, 0.975)
        ],
        "receipts_with_training_pairs": sum(
            bool(training_pairs([r])) for r in receipts
        ),
        "items_with_multiple_candidates": sum(
            len(c) > 1 for r in receipts for c in r["choices"]
        ),
        "ungated_overrides": sum(r["ungated_changed"] for r in results),
        "ungated_override_coverage": sum(r["ungated_changed"] for r in results)
        / total,
        "protocol": "leave-canonical-merchant-out; frozen hyperparameters; manual labels only",
        "receipts": len(receipts),
        "merchants": len(folds),
        "decoded_items_including_discounts": sum(
            len(r["pred"]) for r in receipts
        ),
        "manual_items_including_discounts": sum(
            len(r["truth"]) for r in receipts
        ),
        "training_pairs_full_dataset": len(pairs),
        "summary": summary,
        "overrides": changed,
        "override_coverage": changed / total if total else 0,
        "abstention_rate": 1 - changed / total if total else 1,
        "per_merchant": per_merchant,
        "per_receipt": results,
        "fixture_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (golden_path, ocr_path)
        },
    }
    model = {
        "experiment_source_sha256": report["experiment_source_sha256"],
        "hyperparameters": report["hyperparameters"],
        "evaluation_split_sha256": {
            fold["merchant"]: fold["split_sha256"] for fold in folds
        },
        "exclude_uncertain": exclude_uncertain,
        "experimental_only": True,
        "enabled_by_default": False,
        "features": FEATURES,
        "weights": train(pairs),
        "pairwise_score_threshold": THRESHOLD,
        "training_pairs": len(pairs),
        "training_receipts": len(receipts),
        "warning": "Uncalibrated pairwise score. All-data model is NOT the heldout evaluation model.",
        "fixture_sha256": report["fixture_sha256"],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "model.json").write_text(json.dumps(model, indent=2) + "\n")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def stress_evidence(path: Path, output: Path) -> None:
    """Optional fresh dev export diagnostics; never used for training/scoring."""
    model = json.loads((output / "model.json").read_text())
    evidence = json.loads(path.read_text())
    results = []
    for image in evidence["images"]:
        entities = image["entities"]
        receipt_ids = {
            entity["SK"].split("#")[1]
            for entity in entities.get("RECEIPT_WORD", [])
        }
        if len(receipt_ids) != 1:
            raise ValueError(
                "Stress export must contain exactly one receipt per image"
            )
        words = []
        for entity in entities.get("RECEIPT_WORD", []):
            identifiers = entity["SK"].split("#")
            box = entity["bounding_box"]
            words.append(
                {
                    "line_id": int(identifiers[3]),
                    "word_id": int(identifiers[5]),
                    "text": entity["text"],
                    "x": box["x"],
                    "y_mid": box["y"] + box["height"] / 2,
                    "h": box["height"],
                }
            )
        ids = {
            line
            for section in entities.get("RECEIPT_SECTION", [])
            if section["section_type"] == "ITEMS"
            for line in section["line_ids"]
        }
        summaries = entities.get("RECEIPT_SUMMARY", [])
        baseline, _ = extract_items(
            words, ids, summary=summaries[0] if summaries else None
        )
        receipt = {
            "pred": baseline,
            "choices": [candidates(p, words) for p in baseline],
        }
        gated, changes = predict(receipt, model["weights"], THRESHOLD)
        ungated, unrestricted_changes = predict(receipt, model["weights"], 0.5)
        results.append(
            {
                "image_id": image["image_id"],
                "changes": changes,
                "ungated_changes": unrestricted_changes,
                **{
                    key: [
                        {"name": p["name"], "price": p["price"]}
                        for p in values
                    ]
                    for key, values in (
                        ("baseline", baseline),
                        ("gated", gated),
                        ("ungated", ungated),
                    )
                },
            }
        )
    (output / "stress.json").write_text(
        json.dumps(
            {
                "warning": "Unscored fresh-receipt stress test, fixed stored sections and summary; no training use",
                "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "results": results,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures", type=Path, default=Path("receipt_upload/tests/fixtures")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stress-evidence", type=Path)
    parser.add_argument("--exclude-uncertain", action="store_true")
    args = parser.parse_args()
    report = run(args.fixtures, args.output, args.exclude_uncertain)
    if args.stress_evidence:
        stress_evidence(args.stress_evidence, args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "receipts",
                    "merchants",
                    "training_pairs_full_dataset",
                    "summary",
                    "override_coverage",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
