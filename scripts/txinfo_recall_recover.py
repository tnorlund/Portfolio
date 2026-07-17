#!/usr/bin/env python3
"""Recover and validate packet labels from an interrupted Claude workflow.

The recall workflow asked its labelers to identify lines using a packet-local
identifier, but the packet JSON did not contain an explicit ``id`` field.
Labelers consequently returned several equivalent spellings.  This utility
normalizes those spellings against the packet contents and refuses to accept a
result unless every packet line maps exactly once.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


UUID_RE = re.compile(
    r"(?P<uuid>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
    re.IGNORECASE,
)
PREFIX_RE = re.compile(r"^(?P<prefix>[0-9a-f]{4,8})", re.IGNORECASE)
RECEIPT_RE = re.compile(
    r"(?:^|[^a-z0-9])r(?P<receipt>\d+)(?=[^a-z0-9]|$)", re.IGNORECASE
)
LINE_RE = re.compile(
    r"(?:^|[^a-z0-9])l(?P<line>\d+)(?=[^a-z0-9]|$)", re.IGNORECASE
)


@dataclass(frozen=True)
class LineKey:
    image_id: str
    receipt_id: int
    line_id: int

    @property
    def canonical(self) -> str:
        return f"{self.image_id}:{self.receipt_id}:{self.line_id}"


def line_key(line: dict[str, Any]) -> LineKey:
    return LineKey(
        image_id=str(line["image_id"]),
        receipt_id=int(line["receipt_id"]),
        line_id=int(line["line_id"]),
    )


def _numeric_suffixes(raw_id: str) -> tuple[int | None, int | None]:
    receipt_match = RECEIPT_RE.search(raw_id)
    line_match = LINE_RE.search(raw_id)
    if line_match:
        return (
            int(receipt_match.group("receipt")) if receipt_match else None,
            int(line_match.group("line")),
        )

    suffix = UUID_RE.sub("", raw_id, count=1)
    suffix = PREFIX_RE.sub("", suffix, count=1)
    suffix = RECEIPT_RE.sub("", suffix)
    numeric = [int(value) for value in re.findall(r"\d+", suffix)]
    if len(numeric) >= 2:
        return numeric[-2], numeric[-1]
    if len(numeric) == 1:
        return (
            int(receipt_match.group("receipt")) if receipt_match else None,
            numeric[0],
        )
    return None, None


def normalize_label_id(raw_id: str, packet_lines: Iterable[dict[str, Any]]) -> str:
    """Resolve a labeler-supplied id to the packet's canonical line id."""

    keys = [line_key(line) for line in packet_lines]
    by_canonical = {key.canonical: key for key in keys}
    if raw_id in by_canonical:
        return raw_id

    uuid_match = UUID_RE.search(raw_id)
    prefix_match = PREFIX_RE.search(raw_id)
    image_token = (
        uuid_match.group("uuid").lower()
        if uuid_match
        else prefix_match.group("prefix").lower()
        if prefix_match
        else None
    )
    receipt_id, line_id = _numeric_suffixes(raw_id)
    if line_id is None:
        raise ValueError(f"cannot parse label id {raw_id!r}")

    def image_matches(key: LineKey) -> bool:
        if image_token is None:
            return True
        image_id = key.image_id.lower()
        return (
            image_id == image_token
            or image_id.startswith(image_token)
            or (len(image_token) >= 8 and image_id.startswith(image_token[:8]))
        )

    matches = [
        key
        for key in keys
        if image_matches(key)
        and key.line_id == line_id
        and (receipt_id is None or key.receipt_id == receipt_id)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"label id {raw_id!r} matched {len(matches)} packet lines"
        )
    return matches[0].canonical


def load_workflow_results(journal_path: Path) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    with journal_path.open() as journal:
        for line_number, raw in enumerate(journal, start=1):
            record = json.loads(raw)
            result = record.get("result")
            if record.get("type") != "result" or not isinstance(result, dict):
                continue
            if not isinstance(result.get("labels"), list):
                continue
            packet_number = int(result["packet"])
            if packet_number in results:
                raise ValueError(
                    f"duplicate successful result for packet {packet_number} "
                    f"at journal line {line_number}"
                )
            results[packet_number] = result
    return results


def normalize_packet_result(
    packet: dict[str, Any],
    result: dict[str, Any],
    overrides: dict[str, bool] | None = None,
) -> dict[str, Any]:
    lines = packet["lines"]
    expected = {line_key(line).canonical for line in lines}
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label in result["labels"]:
        canonical = normalize_label_id(str(label["id"]), lines)
        if canonical in seen:
            raise ValueError(f"duplicate normalized id {canonical}")
        seen.add(canonical)
        normalized.append(
            {"id": canonical, "is_txinfo": bool(label["is_txinfo"])}
        )

    for canonical, is_txinfo in (overrides or {}).items():
        if canonical not in expected:
            continue
        replacement = {"id": canonical, "is_txinfo": bool(is_txinfo)}
        if canonical in seen:
            normalized = [
                replacement if label["id"] == canonical else label
                for label in normalized
            ]
        else:
            seen.add(canonical)
            normalized.append(replacement)

    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra:
        raise ValueError(
            f"coverage mismatch: {len(missing)} missing {missing[:5]}, "
            f"{len(extra)} extra {extra[:5]}"
        )
    return {
        "packet": str(packet["packet_id"]).removeprefix("packet_"),
        "line_count": len(lines),
        "labels": normalized,
    }


def recover(
    journal_path: Path,
    packets_dir: Path,
    output_dir: Path,
    overrides: dict[str, bool] | None = None,
) -> dict[str, Any]:
    raw_results = load_workflow_results(journal_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    valid_packets: list[int] = []
    invalid_packets: dict[str, str] = {}
    scanned = 0
    proposed_hits = 0
    labeled_lines: list[dict[str, Any]] = []

    packet_paths = sorted(packets_dir.glob("packet_*.json"))
    all_packet_numbers = {
        int(path.stem.removeprefix("packet_")) for path in packet_paths
    }
    for packet_number, result in sorted(raw_results.items()):
        packet_path = packets_dir / f"packet_{packet_number:03d}.json"
        if not packet_path.exists():
            invalid_packets[str(packet_number)] = "packet file missing"
            continue
        packet = json.loads(packet_path.read_text())
        try:
            normalized = normalize_packet_result(packet, result, overrides)
        except (KeyError, TypeError, ValueError) as exc:
            invalid_packets[str(packet_number)] = str(exc)
            continue
        (output_dir / f"packet_{packet_number:03d}.json").write_text(
            json.dumps(normalized, indent=2) + "\n"
        )
        valid_packets.append(packet_number)
        scanned += normalized["line_count"]
        proposed_hits += sum(
            1 for label in normalized["labels"] if label["is_txinfo"]
        )
        label_by_id = {
            label["id"]: label["is_txinfo"] for label in normalized["labels"]
        }
        for source_line in packet["lines"]:
            canonical = line_key(source_line).canonical
            labeled_lines.append(
                {
                    "packet": packet_number,
                    "id": canonical,
                    "is_txinfo": label_by_id[canonical],
                    **source_line,
                }
            )

    missing_packets = sorted(all_packet_numbers - set(valid_packets))
    summary = {
        "packet_count": len(all_packet_numbers),
        "raw_successful_results": len(raw_results),
        "valid_results": len(valid_packets),
        "invalid_results": invalid_packets,
        "missing_packets": missing_packets,
        "scanned": scanned,
        "proposed_hits": proposed_hits,
    }
    (output_dir / "RECOVERY_SUMMARY.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    with (output_dir / "LABELED_LINES.jsonl").open("w") as output:
        for item in labeled_lines:
            output.write(json.dumps(item, sort_keys=True) + "\n")
    positives = [item for item in labeled_lines if item["is_txinfo"]]
    (output_dir / "POSITIVE_CANDIDATES.json").write_text(
        json.dumps(positives, indent=2, sort_keys=True) + "\n"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides = json.loads(args.overrides.read_text()) if args.overrides else None
    summary = recover(args.journal, args.packets, args.output, overrides)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
