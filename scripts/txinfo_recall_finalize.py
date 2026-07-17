#!/usr/bin/env python3
"""Finalize a verified recall pass after explicit manual adjudication.

This refuses to produce a correction set while verifier disputes remain. Manual
adjudications are intentionally separate from reusable heuristics so one-off OCR
or receipt-layout decisions remain visible and reviewable.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_adjudications(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("adjudications must be a JSON object keyed by canonical line ID")
    normalized: dict[str, dict[str, Any]] = {}
    for canonical, decision in payload.items():
        if not isinstance(decision, dict):
            raise ValueError(f"adjudication {canonical!r} must be an object")
        if not isinstance(decision.get("is_txinfo"), bool):
            raise ValueError(f"adjudication {canonical!r} needs boolean is_txinfo")
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"adjudication {canonical!r} needs a non-empty reason")
        normalized[str(canonical)] = {
            "is_txinfo": decision["is_txinfo"],
            "reason": reason.strip(),
        }
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results = json.loads(
        (args.verification / "VERIFICATION_RESULTS.json").read_text()
    )
    result_by_id = {item["id"]: item for item in results}
    if len(result_by_id) != len(results):
        raise ValueError("verification results contain duplicate canonical IDs")

    disputes = [item for item in results if item["outcome"] == "DISPUTED"]
    if disputes:
        raise ValueError(f"cannot finalize with {len(disputes)} unresolved disputes")

    adjudications = load_adjudications(args.adjudications)
    unknown = sorted(set(adjudications) - set(result_by_id))
    if unknown:
        raise ValueError(f"adjudications reference unknown verification IDs: {unknown}")

    final = []
    for item in results:
        default = item["outcome"] == "CONFIRMED"
        adjudication = adjudications.get(item["id"])
        is_txinfo = adjudication["is_txinfo"] if adjudication else default
        if not is_txinfo:
            continue
        final.append(
            {
                **item,
                "final_is_txinfo": True,
                "final_source": "manual-adjudication" if adjudication else "dual-verification",
                **(
                    {"adjudication_reason": adjudication["reason"]}
                    if adjudication
                    else {}
                ),
            }
        )

    changed = [
        canonical
        for canonical, decision in adjudications.items()
        if decision["is_txinfo"]
        != (result_by_id[canonical]["outcome"] == "CONFIRMED")
    ]
    summary = {
        "verification_results": len(results),
        "verification_outcomes": dict(
            sorted(Counter(item["outcome"] for item in results).items())
        ),
        "manual_adjudications": len(adjudications),
        "manual_outcome_changes": len(changed),
        "final_txinfo_lines": len(final),
        "final_by_current_label": dict(
            sorted(Counter(item["current_label"] for item in final).items())
        ),
        "method": "dual-verification-plus-explicit-adjudication-v1",
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "FINAL_POSITIVES.json").write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "FINAL_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
