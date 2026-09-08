#!/usr/bin/env python3
"""Non-interactive Dependabot maintenance for GitHub Actions.

This is the orchestration layer used by
``.github/workflows/dependabot-maintenance.yml``. It reuses the guardrails in
``dependabot_maintainer.py`` (``classify`` and friends) instead of parsing the
human-readable report, and verifies the guarded head in an isolated worktree before merging.
Each merge waits for its main Deploy and Smoke Tests before continuing.

Modes:

* ``report``: classify every open Dependabot PR and write a summary.
* ``merge``: additionally squash-merge every PR that is ``ready``, re-running
  the guard immediately before each merge, then ask Dependabot to rebase any
  PR left ``CONFLICTING`` by those merges.

``--dry-run`` performs every read but prints intended merges and rebase
requests instead of calling ``gh pr merge`` / ``gh pr comment``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dependabot_maintainer as maintainer

MODES = ("report", "merge")
STATUS_ORDER = ("ready", "wait", "manual")
MERGE_BODY = (
    "Merged after provenance guards, local verification, and PR CI passed."
)
REBASE_COMMENT = "@dependabot rebase"
UNKNOWN_MERGEABILITY_MARKERS = ("UNKNOWN", "/None", "None/")


@dataclass
class PRRecord:
    """Guardrail classification of one open Dependabot PR."""

    number: int
    title: str
    url: str
    head: str
    status: str
    reasons: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


@dataclass
class MergeResult:
    number: int
    head: str
    merge_sha: str | None
    dry_run: bool
    release_run: int | None = None


@dataclass
class Outcome:
    """Everything the run did, for the step summary and exit code."""

    mode: str
    dry_run: bool
    allow_major: bool
    records: list[PRRecord] = field(default_factory=list)
    merged: list[MergeResult] = field(default_factory=list)
    skipped: list[PRRecord] = field(default_factory=list)
    not_attempted: list[int] = field(default_factory=list)
    rebase_requested: list[int] = field(default_factory=list)
    rebase_refused: list[tuple[int, list[str]]] = field(default_factory=list)
    failure: str | None = None

    @property
    def exit_code(self) -> int:
        return 1 if self.failure else 0


def inspect_pr(
    root: Path,
    repo: str,
    number: int,
    *,
    allow_major: bool,
) -> tuple[dict[str, Any], PRRecord]:
    """Fetch a PR plus its diff and classify it with the shared guardrails."""
    pr = maintainer.pr_view(root, repo, number)
    diff_text = maintainer.pr_diff(root, repo, number)
    status, reasons = maintainer.classify(
        root,
        repo,
        pr,
        allow_major=allow_major,
        diff_text=diff_text,
    )
    record = PRRecord(
        number=pr["number"],
        title=pr["title"],
        url=pr["url"],
        head=pr["headRefOid"],
        status=status,
        reasons=list(reasons),
        paths=maintainer.changed_paths(pr),
    )
    return pr, record


def collect_records(
    root: Path,
    repo: str,
    *,
    limit: int,
    allow_major: bool,
) -> list[PRRecord]:
    listed = maintainer.pr_list(root, repo, limit)
    records: list[PRRecord] = []
    for item in listed:
        _, record = inspect_pr(
            root, repo, item["number"], allow_major=allow_major
        )
        records.append(record)
    return records


def select_ready(records: list[PRRecord]) -> list[PRRecord]:
    """Return ``ready`` PRs oldest-first so merges stay auditable."""
    return sorted(
        (record for record in records if record.status == "ready"),
        key=lambda record: record.number,
    )


def format_report(records: list[PRRecord]) -> str:
    if not records:
        return "No open Dependabot PRs.\n"

    lines: list[str] = []
    for record in records:
        lines.append(f"## PR #{record.number}: {record.title}")
        lines.append(f"URL: {record.url}")
        lines.append(f"Head: {record.head}")
        lines.append(f"Status: {record.status}")
        lines.append("Files:")
        lines.extend(f"- {path}" for path in record.paths)
        if record.reasons:
            lines.append("Reasons:")
            lines.extend(f"- {reason}" for reason in record.reasons)
        lines.append("")
    return "\n".join(lines) + "\n"


def mergeability_unsettled(record: PRRecord) -> bool:
    """True when GitHub has not finished recomputing mergeability yet.

    Right after a merge to ``main`` GitHub reports sibling PRs as
    ``UNKNOWN`` for a few seconds; retrying the guard avoids skipping a PR
    that is about to become ``CLEAN`` or ``CONFLICTING``.
    """
    return any(
        reason.startswith("mergeability must be")
        and any(marker in reason for marker in UNKNOWN_MERGEABILITY_MARKERS)
        for reason in record.reasons
    )


def guard_before_merge(
    root: Path,
    repo: str,
    number: int,
    *,
    allow_major: bool,
    retries: int,
    retry_delay: float,
) -> tuple[dict[str, Any], PRRecord]:
    pr, record = inspect_pr(root, repo, number, allow_major=allow_major)
    attempt = 0
    while mergeability_unsettled(record) and attempt < retries:
        attempt += 1
        print(
            f"PR #{number}: mergeability unsettled, retry {attempt}/{retries}",
            file=sys.stderr,
        )
        time.sleep(retry_delay)
        pr, record = inspect_pr(root, repo, number, allow_major=allow_major)
    return pr, record


def merge_pr(
    root: Path,
    repo: str,
    pr: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    subject = pr["title"].split(" from ", 1)[0]
    cmd = [
        "gh",
        "pr",
        "merge",
        str(pr["number"]),
        "--repo",
        repo,
        "--squash",
        "--delete-branch",
        "--match-head-commit",
        pr["headRefOid"],
        "--subject",
        subject,
        "--body",
        MERGE_BODY,
    ]
    if dry_run:
        print(f"DRY RUN: would run: {' '.join(cmd)}")
        return
    maintainer.run(cmd, cwd=root)


def merge_commit_sha(root: Path, repo: str, number: int) -> str | None:
    data = maintainer.run_json(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "mergeCommit",
        ],
        cwd=root,
    )
    merge_commit = data.get("mergeCommit") or {}
    return merge_commit.get("oid")


def current_main_sha(root: Path, repo: str) -> str:
    return maintainer.run_json(
        ["gh", "api", f"repos/{repo}/commits/main"], cwd=root
    )["sha"]


def wait_for_main_release(
    root: Path,
    repo: str,
    sha: str,
    *,
    timeout: float = 1800,
    poll_delay: float = 15,
) -> int:
    """Require successful deployment and smoke jobs for this exact main SHA."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = maintainer.run_json(
            [
                "gh",
                "api",
                f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100",
            ],
            cwd=root,
        )["workflow_runs"]
        matches = [
            run
            for run in runs
            if run.get("path") == ".github/workflows/main.yml"
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == sha
        ]
        if matches:
            run = max(
                matches,
                key=lambda item: (item["id"], item.get("run_attempt", 1)),
            )
            if run["status"] == "completed":
                jobs = maintainer.run_json(
                    [
                        "gh",
                        "api",
                        f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100",
                    ],
                    cwd=root,
                )["jobs"]
                gates = {
                    job["name"]: job.get("conclusion")
                    for job in jobs
                    if job["name"] in {"Deploy", "Smoke Tests"}
                }
                if run.get("conclusion") != "success" or gates != {
                    "Deploy": "success",
                    "Smoke Tests": "success",
                }:
                    raise RuntimeError(
                        f"Main release {sha} failed or lacks successful Deploy/Smoke Tests: {run['html_url']}"
                    )
                return run["id"]
        time.sleep(poll_delay)
    raise TimeoutError(
        f"Main release {sha} did not complete within {timeout:g} seconds"
    )


def verify_candidate(
    root: Path, repo: str, number: int, *, allow_major: bool
) -> None:
    """Run the shared verifier without forwarding the maintenance write token."""
    command = [
        sys.executable,
        str(Path(maintainer.__file__).resolve()),
        "--repo",
        repo,
        "verify",
        str(number),
    ]
    if allow_major:
        command.append("--allow-major")
    env = dict(os.environ)
    read_token = env.pop("VERIFICATION_GH_TOKEN", None)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "DEPENDABOT_MAINTAINER_TOKEN"):
        env.pop(key, None)
    if read_token:
        env["GH_TOKEN"] = read_token
    elif env.get("GITHUB_ACTIONS") == "true":
        raise RuntimeError(
            "Verification requires its separate read-only GitHub token"
        )
    subprocess.run(command, cwd=root, env=env, check=True, timeout=1200)


def run_merge_phase(
    root: Path,
    repo: str,
    outcome: Outcome,
    *,
    retries: int,
    retry_delay: float,
    max_merges: int,
) -> None:
    candidates = select_ready(outcome.records)
    for index, candidate in enumerate(candidates):
        if len(outcome.merged) >= max_merges:
            outcome.not_attempted.extend(
                item.number for item in candidates[index:]
            )
            break
        number = candidate.number
        try:
            pr, record = guard_before_merge(
                root,
                repo,
                number,
                allow_major=outcome.allow_major,
                retries=retries,
                retry_delay=retry_delay,
            )
            if record.status != "ready":
                outcome.skipped.append(record)
                continue
            if not outcome.dry_run:
                main_sha = current_main_sha(root, repo)
                wait_for_main_release(root, repo, main_sha)
                verified_head = record.head
                verify_candidate(
                    root, repo, number, allow_major=outcome.allow_major
                )
                pr, record = guard_before_merge(
                    root,
                    repo,
                    number,
                    allow_major=outcome.allow_major,
                    retries=retries,
                    retry_delay=retry_delay,
                )
                if record.status != "ready" or record.head != verified_head:
                    raise RuntimeError(
                        "PR head or checks changed after local verification"
                    )
                if current_main_sha(root, repo) != main_sha:
                    raise RuntimeError(
                        "Main advanced during verification; re-run against its release"
                    )
            merge_pr(root, repo, pr, dry_run=outcome.dry_run)
            merge_sha = (
                None
                if outcome.dry_run
                else merge_commit_sha(root, repo, number)
            )
            result = MergeResult(
                number=number,
                head=record.head,
                merge_sha=merge_sha,
                dry_run=outcome.dry_run,
            )
            outcome.merged.append(result)
            if not outcome.dry_run:
                if not merge_sha:
                    raise RuntimeError(
                        "Merge returned no commit SHA; release is unverified"
                    )
                result.release_run = wait_for_main_release(
                    root, repo, merge_sha
                )
        except (subprocess.SubprocessError, RuntimeError, OSError) as exc:
            outcome.failure = (
                f"PR #{number}: {exc}; stopped before further merges"
            )
            outcome.not_attempted.extend(
                item.number for item in candidates[index + 1 :]
            )
            print(outcome.failure, file=sys.stderr)
            return


def request_rebase(
    root: Path,
    repo: str,
    number: int,
    *,
    dry_run: bool,
) -> list[str]:
    """Post ``@dependabot rebase`` after the same guard as ``rebase``."""
    pr = maintainer.pr_view(root, repo, number)
    reasons = maintainer.base_guard_reasons(root, repo, pr)
    if reasons:
        return reasons
    cmd = [
        "gh",
        "pr",
        "comment",
        str(number),
        "--repo",
        repo,
        "--body",
        REBASE_COMMENT,
    ]
    if dry_run:
        print(f"DRY RUN: would run: {' '.join(cmd)}")
        return []
    maintainer.run(cmd, cwd=root)
    return []


def run_rebase_phase(
    root: Path,
    repo: str,
    outcome: Outcome,
    *,
    limit: int,
) -> None:
    merged_numbers = {result.number for result in outcome.merged}
    for item in maintainer.pr_list(root, repo, limit):
        number = item["number"]
        if number in merged_numbers:
            continue
        if item.get("mergeable") != "CONFLICTING":
            continue
        reasons = request_rebase(root, repo, number, dry_run=outcome.dry_run)
        if reasons:
            outcome.rebase_refused.append((number, reasons))
        else:
            outcome.rebase_requested.append(number)


def _pr_link(record: PRRecord) -> str:
    return f"[#{record.number}]({record.url})"


def _label(by_number: dict[int, PRRecord], number: int) -> str:
    record = by_number.get(number)
    return _pr_link(record) if record else f"#{number}"


def _section(title: str, body: list[str]) -> list[str]:
    return [f"## {title}", "", *(body or ["None."]), ""]


def _merge_sections(
    outcome: Outcome, by_number: dict[int, PRRecord]
) -> list[str]:
    lines: list[str] = []

    merged_rows: list[str] = []
    if outcome.merged:
        merged_rows = [
            "| PR | Head | Merge commit | Deploy and smoke |",
            "| --- | --- | --- | --- |",
        ]
        for result in outcome.merged:
            record = by_number[result.number]
            merge_sha = result.merge_sha or "(dry run)"
            merged_rows.append(
                f"| {_pr_link(record)} {record.title} "
                f"| `{result.head[:12]}` | `{merge_sha}` | "
                f"{result.release_run or ('dry run' if result.dry_run else 'unverified')} |"
            )
    lines.extend(
        _section("Would merge" if outcome.dry_run else "Merged", merged_rows)
    )

    if outcome.skipped:
        body: list[str] = []
        for record in outcome.skipped:
            body.append(
                f"- {_pr_link(record)} {record.title}: {record.status}"
            )
            body.extend(f"  - {reason}" for reason in record.reasons)
        lines.extend(_section("Skipped at merge time (guard re-check)", body))

    if outcome.not_attempted:
        lines.extend(
            _section(
                "Not attempted (run limit or failure)",
                [f"- {_label(by_number, n)}" for n in outcome.not_attempted],
            )
        )

    lines.extend(
        _section(
            "Would request rebase" if outcome.dry_run else "Rebase requested",
            [f"- {_label(by_number, n)}" for n in outcome.rebase_requested],
        )
    )

    if outcome.rebase_refused:
        body = []
        for number, reasons in outcome.rebase_refused:
            body.append(f"- {_label(by_number, number)}")
            body.extend(f"  - {reason}" for reason in reasons)
        lines.extend(_section("Rebase refused by guard", body))
    return lines


def _remaining_section(outcome: Outcome) -> list[str]:
    merged_numbers = {result.number for result in outcome.merged}
    remaining = [
        record
        for record in outcome.records
        if record.number not in merged_numbers
    ]
    body: list[str] = []
    for status in STATUS_ORDER:
        group = [r for r in remaining if r.status == status]
        if not group:
            continue
        body.extend([f"### {status} ({len(group)})", ""])
        for record in group:
            body.append(f"- {_pr_link(record)} {record.title}")
            body.extend(f"  - {reason}" for reason in record.reasons)
        body.append("")
    return _section("Remaining open PRs", body)


def format_summary(outcome: Outcome) -> str:
    """Render a GitHub step summary (Markdown)."""
    flags = []
    if outcome.dry_run:
        flags.append("dry-run")
    if outcome.allow_major:
        flags.append("allow-major")
    heading = f"# Dependabot maintenance ({outcome.mode}"
    heading += f", {', '.join(flags)})" if flags else ")"

    lines = [heading, ""]
    if outcome.failure:
        lines.extend([f"> **Failure:** {outcome.failure}", ""])

    if not outcome.records:
        lines.append("No open Dependabot PRs.")
        return "\n".join(lines) + "\n"

    counts = ", ".join(
        f"{status}: "
        f"{sum(1 for r in outcome.records if r.status == status)}"
        for status in STATUS_ORDER
    )
    lines.extend([f"Open Dependabot PRs: {len(outcome.records)} {counts}", ""])

    by_number = {record.number: record for record in outcome.records}
    if outcome.mode == "merge":
        lines.extend(_merge_sections(outcome, by_number))
    lines.extend(_remaining_section(outcome))
    return "\n".join(lines).rstrip() + "\n"


def write_summary(text: str, summary_file: str | None) -> None:
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)


def run_maintenance(
    root: Path,
    repo: str,
    *,
    mode: str,
    allow_major: bool,
    dry_run: bool,
    limit: int,
    retries: int,
    retry_delay: float,
    max_merges: int = 3,
) -> Outcome:
    if not 1 <= max_merges <= 3:
        raise ValueError("max_merges must be between 1 and 3")
    outcome = Outcome(mode=mode, dry_run=dry_run, allow_major=allow_major)
    outcome.records = collect_records(
        root, repo, limit=limit, allow_major=allow_major
    )
    print(format_report(outcome.records), end="")

    if mode == "merge":
        run_merge_phase(
            root,
            repo,
            outcome,
            retries=retries,
            retry_delay=retry_delay,
            max_merges=max_merges,
        )
        if outcome.failure is None:
            run_rebase_phase(root, repo, outcome, limit=limit)
    return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub repo in owner/name form")
    parser.add_argument("--mode", choices=MODES, default="report")
    parser.add_argument("--allow-major", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print intended merges and rebase requests without writing",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--max-merges", type=int, choices=range(1, 4), default=3
    )
    parser.add_argument(
        "--guard-retries",
        type=int,
        default=3,
        help="Re-check attempts while GitHub recomputes mergeability",
    )
    parser.add_argument(
        "--guard-retry-delay",
        type=float,
        default=10.0,
        help="Seconds to wait between mergeability re-checks",
    )
    parser.add_argument(
        "--summary-file",
        default=os.environ.get("GITHUB_STEP_SUMMARY"),
        help="Markdown summary destination (default: $GITHUB_STEP_SUMMARY)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = maintainer.repo_root()
    repo = args.repo or maintainer.repo_full_name(root)
    outcome = run_maintenance(
        root,
        repo,
        mode=args.mode,
        allow_major=args.allow_major,
        dry_run=args.dry_run,
        limit=args.limit,
        retries=args.guard_retries,
        retry_delay=args.guard_retry_delay,
        max_merges=args.max_merges,
    )
    write_summary(format_summary(outcome), args.summary_file)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
