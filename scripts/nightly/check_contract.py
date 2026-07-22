#!/usr/bin/env python3
"""Morning-report contract checker (plan humble-skipping-quilt W2).

Validates a nightly morning report against docs/nightly/REPORT_CONTRACT.md:
all seven required ``##`` sections present in order, plus exactly one
parseable verdict line. The nightly wrapper runs this on the agent's
report; a nonzero exit makes the wrapper replace the report with a RED
stub (which itself satisfies this contract).

Usage:
    check_contract.py REPORT.md [--json] [--verdict]

Exit codes:
    0  valid report
    1  invalid report (problems printed one per line to stderr)
    2  file missing or unreadable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Verdict",
    "Budget",
    "Fleet",
    "Tonight's Work",
    "Awaiting Owner",
    "Failures & Anomalies",
    "Tomorrow's Top 3",
)

# **Verdict: GREEN** - sentence   (bold optional, "-" or ":" separator,
# nonempty trailing sentence required). Colors are uppercase-only.
VERDICT_RE = re.compile(
    r"^(?:\*\*)?Verdict:\s*(GREEN|YELLOW|RED)(?:\*\*)?\s*[-:]\s*(\S.*)$"
)

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


def _normalize_heading(title: str) -> str:
    return title.strip().lower()


def check_report_text(text: str) -> dict[str, Any]:
    """Pure contract check; returns {valid, verdict, problems, sections}."""
    problems: list[str] = []

    headings: list[str] = [
        _normalize_heading(m.group(1))
        for line in text.splitlines()
        if (m := HEADING_RE.match(line))
    ]

    # Presence + order: walk the found headings looking for each required
    # section past the position of the previous one.
    cursor = 0
    out_of_order: list[str] = []
    for section in REQUIRED_SECTIONS:
        want = _normalize_heading(section)
        try:
            idx = headings.index(want, cursor)
            cursor = idx + 1
        except ValueError:
            if want in headings:
                out_of_order.append(section)
            else:
                problems.append(f"missing section: ## {section}")
    for section in out_of_order:
        problems.append(f"section out of order: ## {section}")

    verdict_matches = [
        m for line in text.splitlines() if (m := VERDICT_RE.match(line))
    ]
    verdict: str | None = None
    if not verdict_matches:
        problems.append(
            "no parseable verdict line "
            "(need: **Verdict: GREEN|YELLOW|RED** - <one sentence>)"
        )
    else:
        verdict = verdict_matches[0].group(1)
        if len(verdict_matches) > 1:
            problems.append(
                f"multiple verdict lines ({len(verdict_matches)}); "
                "the contract requires exactly one"
            )

    return {
        "valid": not problems,
        "verdict": verdict,
        "problems": problems,
        "sections_found": headings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a nightly morning report against the contract."
    )
    parser.add_argument("report", help="path to the morning report .md")
    parser.add_argument(
        "--json", action="store_true", help="emit the machine-readable result"
    )
    parser.add_argument(
        "--verdict",
        action="store_true",
        help="print only the parsed verdict color (valid reports only)",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"cannot read report: {exc}", file=sys.stderr)
        return 2

    result = check_report_text(text)

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.verdict:
        if result["valid"]:
            print(result["verdict"])
    else:
        status = "VALID" if result["valid"] else "INVALID"
        print(f"contract: {status} verdict={result['verdict'] or 'n/a'}")

    for problem in result["problems"]:
        print(problem, file=sys.stderr)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
