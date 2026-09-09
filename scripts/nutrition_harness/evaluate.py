#!/usr/bin/env python3.13
"""Evaluate nutrition contracts without claiming unevaluated real accuracy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from receipt_nutrition.evaluation import evaluate_contract

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "receipt_nutrition/tests/fixtures/contract_cases.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("contract", "offline", "live"), required=True
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode != "contract":
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "status": "NOT RUN",
                    "reason": "source-backed matching evaluation is not implemented yet",
                    "automatic_acceptance_enabled": False,
                },
                indent=2,
            )
        )
        return 2
    try:
        score = evaluate_contract(args.fixture)
    except (OSError, ValueError, ValidationError) as error:
        print(json.dumps({"status": "INVALID FIXTURE", "error": str(error)}))
        return 2
    rendered = json.dumps(score, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0 if score["contract_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
