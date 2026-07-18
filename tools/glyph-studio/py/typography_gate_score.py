#!/usr/bin/env python3
"""Score blind typography verdicts against the Costco calibration gate."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

import jsonschema
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from glyphstudio.packets import canonical_json

_SCHEMA_PATH = os.path.join(
    _ROOT,
    "tools",
    "glyph-studio",
    "schemas",
    "typography_judge_verdict.schema.json",
)
_RUNNER_ABSTAIN_REASON = "runner: invalid or missing verdict after retry"
_FAMILIES = (
    "mono",
    "sans",
    "serif",
    "slab",
    "script",
    "decorative",
    "unknown",
)
_ATTRIBUTES = (
    "family_class",
    "monospace",
    "italic",
    "weight",
    "tier",
    "underline",
    "reverse_video",
    "slant_deg",
)
_AGREEMENT_ATTRIBUTES = (
    "family_class",
    "monospace",
    "italic",
    "weight",
    "underline",
    "reverse_video",
    "slant_deg",
)
_CONFIDENCE_ATTRIBUTES = (
    "typeface",
    "tier",
    "underline",
    "reverse_video",
    "slant",
)


def _rounded(value: float) -> float:
    return round(float(value), 6)


def _rate(numerator: int, denominator: int) -> float | None:
    return _rounded(numerator / denominator) if denominator else None


def _load_manifest(out_dir: str) -> dict:
    pointer = Path(out_dir, "MANIFEST").read_text(encoding="ascii").strip()
    if not pointer or Path(pointer).name != pointer:
        raise ValueError("MANIFEST must name a file in --out-dir")
    with open(os.path.join(out_dir, pointer), encoding="ascii") as fh:
        return json.load(fh)


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _verdict_path(out_dir: str, judge: str, content_hash: str) -> str:
    return os.path.join(
        out_dir,
        "judging",
        f"verdicts_{judge}_{content_hash[:16]}.json",
    )


def _load_verdicts(
    out_dir: str, judge: str, manifest: dict, schema: dict
) -> dict[str, dict]:
    path = _verdict_path(out_dir, judge, manifest["content_hash"])
    with open(path, encoding="ascii") as fh:
        values = json.load(fh)
    if not isinstance(values, list):
        raise ValueError(f"{path}: expected a JSON array")
    expected_ids = {packet["packet_id"] for packet in manifest["packets"]}
    verdicts: dict[str, dict] = {}
    for verdict in values:
        try:
            jsonschema.validate(verdict, schema)
        except jsonschema.ValidationError as exc:
            raise ValueError(
                f"{path}: invalid verdict: {exc.message}"
            ) from exc
        packet_id = verdict["packet_id"]
        if verdict["judge"] != judge:
            raise ValueError(f"{path}: verdict judge mismatch for {packet_id}")
        if verdict["manifest_content_hash"] != manifest["content_hash"]:
            raise ValueError(f"{path}: stale manifest verdict for {packet_id}")
        if packet_id not in expected_ids:
            raise ValueError(f"{path}: unknown packet_id {packet_id}")
        if packet_id in verdicts:
            raise ValueError(f"{path}: duplicate packet_id {packet_id}")
        verdicts[packet_id] = verdict
    missing = sorted(expected_ids - verdicts.keys())
    if missing:
        raise ValueError(f"{path}: missing verdicts: {', '.join(missing)}")
    return verdicts


def _attribute(verdict: dict, attribute: str) -> Any:
    if attribute in {"family_class", "monospace", "italic", "weight"}:
        typeface = verdict.get("typeface")
        return typeface.get(attribute) if isinstance(typeface, dict) else None
    return verdict.get(attribute)


def _answer_key(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return value
    return canonical_json(value)


def _answer_distribution(verdicts: list[dict]) -> dict[str, dict[str, int]]:
    allowed: dict[str, list[Any]] = {
        "family_class": [*_FAMILIES, None],
        "monospace": [True, False, None],
        "italic": [True, False, None],
        "weight": ["normal", "bold", "unknown", None],
        "tier": ["normal", "bold", "large", "unknown", None],
        "underline": [True, False, None],
        "reverse_video": [True, False, None],
        "slant_deg": [None],
    }
    result = {}
    for attribute in _ATTRIBUTES:
        counts = Counter(
            _answer_key(_attribute(verdict, attribute)) for verdict in verdicts
        )
        keys = {_answer_key(value) for value in allowed[attribute]} | set(
            counts
        )
        result[attribute] = {key: counts[key] for key in sorted(keys, key=str)}
    return result


def _confidence_spreads(verdicts: list[dict]) -> dict[str, dict]:
    result = {}
    for attribute in _CONFIDENCE_ATTRIBUTES:
        values = [
            float(verdict["confidence"][attribute])
            for verdict in verdicts
            if isinstance(verdict.get("confidence"), dict)
            and verdict["confidence"].get(attribute) is not None
        ]
        if not values:
            result[attribute] = {
                "n": 0,
                "min": None,
                "mean": None,
                "max": None,
                "stddev": None,
            }
            continue
        result[attribute] = {
            "n": len(values),
            "min": _rounded(min(values)),
            "mean": _rounded(fmean(values)),
            "max": _rounded(max(values)),
            "stddev": _rounded(pstdev(values)),
        }
    return result


def _slant_metrics(
    verdicts: list[dict], packets_by_id: dict[str, dict]
) -> dict:
    judged_slants = [
        float(verdict["slant_deg"])
        for verdict in verdicts
        if verdict.get("slant_deg") is not None
    ]
    pairs = [
        (
            float(verdict["slant_deg"]),
            float(
                packets_by_id[verdict["packet_id"]]["features"]["slant_deg"]
            ),
        )
        for verdict in verdicts
        if verdict.get("slant_deg") is not None
        and packets_by_id[verdict["packet_id"]]["features"].get("slant_deg")
        is not None
    ]
    pearson = None
    if len(pairs) >= 2:
        judged = np.asarray([pair[0] for pair in pairs], dtype=float)
        measured = np.asarray([pair[1] for pair in pairs], dtype=float)
        if np.ptp(judged) > 0 and np.ptp(measured) > 0:
            pearson = _rounded(np.corrcoef(judged, measured)[0, 1])
    distribution = {key: None for key in ("min", "p25", "p50", "p75", "max")}
    if judged_slants:
        quantiles = np.percentile(
            np.asarray(judged_slants, dtype=float), [0, 25, 50, 75, 100]
        )
        distribution = {
            key: _rounded(value)
            for key, value in zip(distribution, quantiles, strict=True)
        }
    return {
        "n_compared": len(pairs),
        "pearson_r": pearson,
        "mean_abs_error": (
            _rounded(
                fmean(abs(judged - measured) for judged, measured in pairs)
            )
            if pairs
            else None
        ),
        "judged_distribution": distribution,
    }


def judge_metrics(
    verdicts_by_id: dict[str, dict], packets_by_id: dict[str, dict]
) -> dict:
    """Compute one judge's metrics over its non-abstaining verdicts."""
    n_packets = len(packets_by_id)
    all_verdicts = [
        verdicts_by_id[packet_id] for packet_id in sorted(packets_by_id)
    ]
    abstains = [verdict for verdict in all_verdicts if verdict["abstain"]]
    judged = [verdict for verdict in all_verdicts if not verdict["abstain"]]
    runner_abstains = [
        verdict
        for verdict in abstains
        if verdict.get("abstain_reason") == _RUNNER_ABSTAIN_REASON
    ]
    judge_abstains = [
        verdict
        for verdict in abstains
        if verdict.get("abstain_reason") != _RUNNER_ABSTAIN_REASON
    ]
    family_counts = Counter(
        _attribute(verdict, "family_class") for verdict in judged
    )
    family_dist = {family: family_counts[family] for family in _FAMILIES}
    family_dist["null"] = family_counts[None]

    bold_lines = [
        verdict
        for verdict in judged
        if packets_by_id[verdict["packet_id"]]["features"].get("tier")
        == "bold"
    ]
    normal_lines = [
        verdict
        for verdict in judged
        if packets_by_id[verdict["packet_id"]]["features"].get("tier")
        == "normal"
    ]
    monospace_correct = sum(
        _attribute(verdict, "monospace") is True for verdict in judged
    )
    italic_correct = sum(
        _attribute(verdict, "italic") is False for verdict in judged
    )
    family_ok = sum(
        _attribute(verdict, "family_class") in {"mono", "unknown"}
        for verdict in judged
    )
    return {
        "n_packets": n_packets,
        "n_judged": len(judged),
        "n_abstain": len(abstains),
        "n_runner_abstain": len(runner_abstains),
        "n_judge_abstain": len(judge_abstains),
        "abstain_rate": _rate(len(abstains), n_packets),
        "runner_abstain_rate": _rate(len(runner_abstains), n_packets),
        "judge_abstain_rate": _rate(len(judge_abstains), n_packets),
        "monospace_acc": _rate(monospace_correct, len(judged)),
        "monospace_null_rate": _rate(
            sum(
                _attribute(verdict, "monospace") is None for verdict in judged
            ),
            len(judged),
        ),
        "italic_acc": _rate(italic_correct, len(judged)),
        "italic_null_rate": _rate(
            sum(_attribute(verdict, "italic") is None for verdict in judged),
            len(judged),
        ),
        "family_class_dist": family_dist,
        "family_ok_rate": _rate(family_ok, len(judged)),
        "family_class_confusion": {"truth_mono": family_dist.copy()},
        "weight_discounted": {
            "label": "DISCOUNTED: compared with measured tier",
            "n_measured_bold": len(bold_lines),
            "bold_recall": _rate(
                sum(
                    _attribute(verdict, "weight") == "bold"
                    for verdict in bold_lines
                ),
                len(bold_lines),
            ),
            "n_measured_normal": len(normal_lines),
            "normal_rate": _rate(
                sum(
                    _attribute(verdict, "weight") == "normal"
                    for verdict in normal_lines
                ),
                len(normal_lines),
            ),
        },
        "slant": _slant_metrics(judged, packets_by_id),
        "confidence_spreads": _confidence_spreads(judged),
        "answer_distribution": _answer_distribution(judged),
    }


def _attributes_agree(attribute: str, left: Any, right: Any) -> bool:
    if attribute == "slant_deg":
        if left is None or right is None:
            return left is right
        return abs(float(left) - float(right)) <= 3.0
    return left == right


def agreement_metrics(
    codex: dict[str, dict], grok: dict[str, dict]
) -> tuple[dict, list[str]]:
    packet_ids = sorted(set(codex) & set(grok))
    paired = [
        packet_id
        for packet_id in packet_ids
        if not codex[packet_id]["abstain"] and not grok[packet_id]["abstain"]
    ]
    results = {"n_co_non_abstain": len(paired), "attributes": {}}
    for attribute in _AGREEMENT_ATTRIBUTES:
        agreed = sum(
            _attributes_agree(
                attribute,
                _attribute(codex[packet_id], attribute),
                _attribute(grok[packet_id], attribute),
            )
            for packet_id in paired
        )
        results["attributes"][attribute] = {
            "n_agree": agreed,
            "n_compared": len(paired),
            "agreement_rate": _rate(agreed, len(paired)),
        }
    queue = [
        packet_id
        for packet_id in paired
        if any(
            not _attributes_agree(
                attribute,
                _attribute(codex[packet_id], attribute),
                _attribute(grok[packet_id], attribute),
            )
            for attribute in ("family_class", "monospace", "italic", "weight")
        )
    ]
    return results, queue


def _confidence_for(verdict: dict, attribute: str) -> float:
    confidence_key = {
        "family_class": "typeface",
        "monospace": "typeface",
        "italic": "typeface",
        "weight": "typeface",
        "slant_deg": "slant",
    }.get(attribute, attribute)
    confidence = verdict.get("confidence")
    if not isinstance(confidence, dict):
        return 0.0
    return float(confidence.get(confidence_key, 0.0))


def _resolution_value(resolution: dict, attribute: str) -> tuple[bool, Any]:
    if attribute in resolution:
        return True, resolution[attribute]
    if attribute in {"family_class", "monospace", "italic", "weight"}:
        typeface = resolution.get("typeface")
        if isinstance(typeface, dict) and attribute in typeface:
            return True, typeface[attribute]
    return False, None


def _final_verdicts(
    packets_by_id: dict[str, dict],
    codex: dict[str, dict],
    grok: dict[str, dict],
    adjudication: list[dict],
) -> tuple[dict[str, dict], list[dict]]:
    decisions = {item["packet_id"]: item for item in adjudication}
    unknown = sorted(set(decisions) - set(packets_by_id))
    if unknown:
        raise ValueError(
            "adjudication has unknown packet_ids: " + ", ".join(unknown)
        )
    verdicts = {}
    labels = []
    for packet_id in sorted(packets_by_id):
        decision = decisions.get(packet_id, {})
        resolution = decision.get("resolution", {})
        if not isinstance(resolution, dict):
            raise ValueError(
                f"{packet_id}: adjudication resolution must be an object"
            )
        values = {}
        sources = {}
        for attribute in _ATTRIBUTES:
            resolved, value = _resolution_value(resolution, attribute)
            if resolved:
                values[attribute] = value
                sources[attribute] = "adjudication"
                continue
            available = [
                verdict
                for verdict in (codex[packet_id], grok[packet_id])
                if not verdict["abstain"]
            ]
            if len(available) == 2 and _attributes_agree(
                attribute,
                _attribute(available[0], attribute),
                _attribute(available[1], attribute),
            ):
                left = _attribute(available[0], attribute)
                right = _attribute(available[1], attribute)
                if attribute == "slant_deg" and left is not None:
                    values[attribute] = _rounded(
                        (float(left) + float(right)) / 2
                    )
                else:
                    values[attribute] = left
                sources[attribute] = "agreement"
            elif (
                len(available) == 1
                and _confidence_for(available[0], attribute) >= 0.8
            ):
                values[attribute] = _attribute(available[0], attribute)
                sources[attribute] = "single_judge_high_confidence"
            else:
                values[attribute] = None
                sources[attribute] = "unresolved"
        abstain = all(value is None for value in values.values())
        verdicts[packet_id] = {
            "packet_id": packet_id,
            "abstain": abstain,
            "abstain_reason": "final: unresolved" if abstain else None,
            "typeface": (
                {
                    attribute: values[attribute]
                    for attribute in (
                        "family_class",
                        "monospace",
                        "italic",
                        "weight",
                    )
                }
                if not abstain
                else None
            ),
            "tier": values["tier"] if not abstain else None,
            "underline": values["underline"] if not abstain else None,
            "reverse_video": (
                values["reverse_video"] if not abstain else None
            ),
            "slant_deg": values["slant_deg"] if not abstain else None,
            "confidence": None,
        }
        labels.append(
            {
                "packet_id": packet_id,
                "label": values,
                "sources": sources,
                "adjudicator_note": decision.get("adjudicator_note"),
            }
        )
    return verdicts, labels


def _pass_bars(metrics: dict) -> dict:
    bars = {
        "monospace_acc": metrics["monospace_acc"] is not None
        and metrics["monospace_acc"] >= 0.90,
        "italic_acc": metrics["italic_acc"] is not None
        and metrics["italic_acc"] >= 0.90,
        "family_ok_rate": metrics["family_ok_rate"] is not None
        and metrics["family_ok_rate"] >= 0.70,
        "abstain_rate": metrics["abstain_rate"] is not None
        and metrics["abstain_rate"] <= 0.15,
    }
    bars["judge_pass"] = all(bars.values())
    return bars


def _write_canonical(path: str, value: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as fh:
        fh.write(canonical_json(value) + "\n")


def score(out_dir: str, adjudication_path: str | None = None) -> dict:
    out_dir = os.path.abspath(out_dir)
    manifest = _load_manifest(out_dir)
    schema = _load_schema()
    packets_by_id = {
        packet["packet_id"]: packet for packet in manifest["packets"]
    }
    # Stratify by MEASURED tier (owner steer): body-tier lines score
    # against the all-mono bitMatrix truth and carry the pass bars;
    # large/display-tier lines (the wordmark class) may legitimately be a
    # different non-mono face, so they get their own stratum and never
    # count against the monospace bar.
    body_packets = {
        packet_id: packet
        for packet_id, packet in packets_by_id.items()
        if packet["features"].get("tier") in {"normal", "bold"}
    }
    display_packets = {
        packet_id: packet
        for packet_id, packet in packets_by_id.items()
        if packet_id not in body_packets
    }
    verdicts = {
        judge: _load_verdicts(out_dir, judge, manifest, schema)
        for judge in ("codex", "grok")
    }
    judge_scores = {
        judge: judge_metrics(values, body_packets)
        for judge, values in verdicts.items()
    }
    display_scores = {
        judge: judge_metrics(values, display_packets)
        for judge, values in verdicts.items()
    }
    all_scores = {
        judge: judge_metrics(values, packets_by_id)
        for judge, values in verdicts.items()
    }
    shortfall = {}
    for judge in verdicts:
        judged_all = [
            verdicts[judge][packet_id]
            for packet_id in packets_by_id
            if not verdicts[judge][packet_id]["abstain"]
        ]
        errors_all = sum(
            _attribute(verdict, "monospace") is not True
            for verdict in judged_all
        )
        errors_display = sum(
            _attribute(verdict, "monospace") is not True
            for verdict in judged_all
            if verdict["packet_id"] in display_packets
        )
        shortfall[judge] = {
            "monospace_acc_all": all_scores[judge]["monospace_acc"],
            "monospace_acc_body": judge_scores[judge]["monospace_acc"],
            "n_monospace_errors_all": errors_all,
            "n_monospace_errors_display": errors_display,
            "display_explained_fraction": _rate(errors_display, errors_all),
        }
    agreement, queue = agreement_metrics(verdicts["codex"], verdicts["grok"])
    bars = {
        judge: _pass_bars(metrics) for judge, metrics in judge_scores.items()
    }
    result = {
        "schema_version": "typography-gate-score-2",
        "manifest_content_hash": manifest["content_hash"],
        "ground_truth": {
            "calibration": "Costco",
            "family_class": "mono",
            "monospace": True,
            "italic": False,
            "truth_scope": "body stratum (measured tier normal|bold)",
        },
        "stratification": {
            "body_tiers": ["normal", "bold"],
            "display_tiers": ["large"],
            "n_body": len(body_packets),
            "n_display": len(display_packets),
        },
        "judges": judge_scores,
        "display_stratum": display_scores,
        "all_lines": all_scores,
        "monospace_shortfall": shortfall,
        "agreement": agreement,
        "pass_bars": bars,
        "gate_pass": all(value["judge_pass"] for value in bars.values()),
    }
    if adjudication_path is not None:
        with open(adjudication_path, encoding="utf-8") as fh:
            adjudication = json.load(fh)
        if not isinstance(adjudication, list):
            raise ValueError("adjudication file must contain a JSON array")
        final_verdicts, labels = _final_verdicts(
            packets_by_id,
            verdicts["codex"],
            verdicts["grok"],
            adjudication,
        )
        result["final"] = {
            "metrics": judge_metrics(final_verdicts, body_packets),
            "metrics_display": judge_metrics(final_verdicts, display_packets),
            "labels": labels,
        }

    judging_dir = os.path.join(out_dir, "judging")
    _write_canonical(
        os.path.join(judging_dir, "adjudication_queue.json"), queue
    )
    _write_canonical(os.path.join(judging_dir, "gate_score.json"), result)
    return result


def _print_score(result: dict) -> None:
    print(f"manifest={result['manifest_content_hash']}")
    strata = result["stratification"]
    print(f"strata: body={strata['n_body']} display={strata['n_display']}")
    for judge in ("codex", "grok"):
        metrics = result["judges"][judge]
        bars = result["pass_bars"][judge]
        short = result["monospace_shortfall"][judge]
        print(
            f"{judge} [body]: judged={metrics['n_judged']}/"
            f"{metrics['n_packets']} abstain={metrics['abstain_rate']} "
            f"monospace={metrics['monospace_acc']} "
            f"italic={metrics['italic_acc']} "
            f"family_ok={metrics['family_ok_rate']} "
            f"pass={bars['judge_pass']}"
        )
        print(
            f"{judge} [shortfall]: all={short['monospace_acc_all']} "
            f"body={short['monospace_acc_body']} display_explained="
            f"{short['display_explained_fraction']}"
        )
    print(
        "co_non_abstain="
        f"{result['agreement']['n_co_non_abstain']} "
        f"gate_pass={result['gate_pass']}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--adjudication", default=None)
    args = parser.parse_args(argv)
    try:
        result = score(args.out_dir, args.adjudication)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_score(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
