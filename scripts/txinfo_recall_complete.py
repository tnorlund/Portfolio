#!/usr/bin/env python3
"""Create complete, keep-biased labels for packets missed by the workflow.

Every line receives a boolean.  Suggestions are treated as first-pass positives
except card-authorization timestamps, which are an explicit PAYMENT exception.
All positives still require the separate two-lens verification pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from txinfo_recall_recover import line_key


def suggestion_is_positive(item: dict[str, Any]) -> bool:
    return "payment-block-datetime" not in item.get("reasons", [])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--suggestions", type=Path, required=True)
    parser.add_argument("--packet-numbers", type=int, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    suggestions = json.loads(args.suggestions.read_text())
    positive_ids = {
        item["id"] for item in suggestions if suggestion_is_positive(item)
    }
    args.output.mkdir(parents=True, exist_ok=True)

    scanned = 0
    positives = 0
    per_packet = []
    for packet_number in args.packet_numbers:
        packet_path = args.packets / f"packet_{packet_number:03d}.json"
        packet = json.loads(packet_path.read_text())
        labels = []
        for line in packet["lines"]:
            canonical = line_key(line).canonical
            is_txinfo = canonical in positive_ids
            labels.append({"id": canonical, "is_txinfo": is_txinfo})
            scanned += 1
            positives += int(is_txinfo)
        result = {
            "packet": str(packet_number),
            "line_count": len(labels),
            "method": "root-review-high-recall-v1",
            "labels": labels,
        }
        (args.output / f"packet_{packet_number:03d}.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        per_packet.append(
            {
                "packet": packet_number,
                "scanned": len(labels),
                "positives": sum(label["is_txinfo"] for label in labels),
            }
        )

    summary = {
        "method": "root-review-high-recall-v1",
        "scanned": scanned,
        "positives": positives,
        "explicit_exception": "payment-block-datetime stays PAYMENT",
        "per_packet": per_packet,
    }
    (args.output / "COMPLETION_SUMMARY.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
