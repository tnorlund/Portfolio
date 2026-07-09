#!/usr/bin/env python3
"""Guardrails and local checks for Portfolio Dependabot PR maintenance."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PASSING_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}
DEPENDABOT_AUTHORS = {"dependabot[bot]", "app/dependabot"}
DEPENDENCY_FILE_PATTERNS = (
    re.compile(r"(^|/)pyproject\.toml$"),
    re.compile(r"(^|/)requirements[^/]*\.txt$"),
    re.compile(r"(^|/)package\.json$"),
    re.compile(r"(^|/)package-lock\.json$"),
    re.compile(r"(^|/)npm-shrinkwrap\.json$"),
    re.compile(r"(^|/)pnpm-lock\.yaml$"),
    re.compile(r"(^|/)yarn\.lock$"),
    re.compile(r"(^|/)uv\.lock$"),
    re.compile(r"(^|/)poetry\.lock$"),
    re.compile(r"(^|/)Pipfile\.lock$"),
    re.compile(r"^\.github/workflows/[^/]+\.(ya?ml)$"),
    re.compile(r"^\.github/dependabot\.yml$"),
)
VERSION_PAIR_RE = re.compile(
    r"\bfrom\s+`?([^`\s]+)`?\s+to\s+`?([^`\s]+)`?",
    re.IGNORECASE,
)


def is_dependency_path(path: str | None) -> bool:
    return bool(path) and is_dependency_file(path)


def run(
    cmd: list[str],
    *,
    cwd: Path,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "text": True,
        "check": check,
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    print(f"+ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, **kwargs)


def run_json(cmd: list[str], *, cwd: Path) -> Any:
    completed = run(cmd, cwd=cwd, capture=True)
    return json.loads(completed.stdout)


def repo_root() -> Path:
    completed = run(["git", "rev-parse", "--show-toplevel"], cwd=Path.cwd(), capture=True)
    return Path(completed.stdout.strip())


def repo_full_name(root: Path) -> str:
    data = run_json(["gh", "repo", "view", "--json", "nameWithOwner"], cwd=root)
    return data["nameWithOwner"]


def pr_list(root: Path, repo: str, limit: int) -> list[dict[str, Any]]:
    return run_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--author",
            "app/dependabot",
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,headRefOid,mergeable,mergeStateStatus",
        ],
        cwd=root,
    )


def pr_view(root: Path, repo: str, number: int) -> dict[str, Any]:
    return run_json(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            ",".join(
                [
                    "author",
                    "baseRefName",
                    "body",
                    "commits",
                    "files",
                    "headRefName",
                    "headRefOid",
                    "isDraft",
                    "mergeStateStatus",
                    "mergeable",
                    "number",
                    "state",
                    "statusCheckRollup",
                    "title",
                    "url",
                ]
            ),
        ],
        cwd=root,
    )


def changed_paths(pr: dict[str, Any]) -> list[str]:
    return sorted(item["path"] for item in pr.get("files", []))


def is_dependency_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in DEPENDENCY_FILE_PATTERNS)


def checks_green(pr: dict[str, Any]) -> tuple[bool, list[str]]:
    checks = pr.get("statusCheckRollup") or []
    if not checks:
        return False, ["missing checks"]

    blockers: list[str] = []
    for check in checks:
        status = check.get("status")
        conclusion = check.get("conclusion") or ""
        name = check.get("name") or check.get("workflowName") or "unknown"
        if status != "COMPLETED" or conclusion not in PASSING_CONCLUSIONS:
            blockers.append(f"{name}: {status}/{conclusion or 'pending'}")
    return not blockers, blockers


def parse_version_pairs(text: str | None) -> list[tuple[str, str]]:
    if not text:
        return []
    return [
        (before.strip("`., "), after.strip("`., "))
        for before, after in VERSION_PAIR_RE.findall(text)
    ]


def leading_major(version: str | None) -> int | None:
    if not version:
        return None
    for token in re.split(r"[, ]+", version):
        token = token.strip()
        if not token or token.startswith("<"):
            continue
        match = re.search(r"(?:[>=~^=]*\s*v?)(\d+)(?:\.\d+)?", token)
        if match:
            return int(match.group(1))

    match = re.search(r"v?(\d+)(?:\.\d+)?(?:\.\d+)?", version)
    if not match:
        return None
    return int(match.group(1))


def pair_is_major(before: str, after: str) -> bool | None:
    before_major = leading_major(before)
    after_major = leading_major(after)
    if before_major is None or after_major is None:
        return None
    return before_major != after_major


def dependency_spec_from_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip().rstrip(",")
    if not stripped:
        return None

    uses_match = re.search(r"\buses:\s+([^@\s]+)@([^\s#]+)", stripped)
    if uses_match:
        return uses_match.group(1), uses_match.group(2)

    json_match = re.match(r'"([^"]+)":\s*"([^"]+)"$', stripped)
    metadata_keys = {"integrity", "license", "name", "resolved", "version"}
    if json_match and json_match.group(1) not in metadata_keys:
        return json_match.group(1), json_match.group(2)

    quoted_match = re.match(r'"([^"<>=~!,\s\[]+)([^"]*)"$', stripped)
    if quoted_match:
        name = quoted_match.group(1)
        spec = quoted_match.group(2).strip()
        return name, spec or name

    req_match = re.match(r"([A-Za-z0-9_.-]+)\s*([<>=!~].*)$", stripped)
    if req_match:
        return req_match.group(1), req_match.group(2)

    return None


def version_pairs_from_diff(diff_text: str) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    current_path: str | None = None
    removed_specs: dict[str, str] = {}

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_path = None
            removed_specs = {}
            parts = line.split()
            if len(parts) >= 4:
                current_path = parts[3].removeprefix("b/")
            continue

        if not is_dependency_path(current_path):
            continue

        if line.startswith("---") or line.startswith("+++"):
            continue

        if line.startswith("-"):
            parsed = dependency_spec_from_line(line[1:])
            if parsed:
                removed_specs[parsed[0]] = parsed[1]
            continue

        if line.startswith("+"):
            parsed = dependency_spec_from_line(line[1:])
            if parsed and parsed[0] in removed_specs:
                pairs.append((parsed[0], removed_specs[parsed[0]], parsed[1]))

    return pairs


def pr_diff(root: Path, repo: str, number: int) -> str:
    completed = run(
        ["gh", "pr", "diff", str(number), "--repo", repo, "--patch"],
        cwd=root,
        capture=True,
    )
    return completed.stdout


def major_update_reasons(
    pr: dict[str, Any],
    *,
    diff_text: str | None,
) -> list[str]:
    text_pairs = parse_version_pairs(pr.get("title")) + parse_version_pairs(
        pr.get("body")
    )
    for before, after in text_pairs:
        result = pair_is_major(before, after)
        if result is True:
            return [f"major-version update detected: {before} -> {after}"]

    if diff_text is None:
        if any(is_dependency_file(path) for path in changed_paths(pr)):
            return ["could not prove update is non-major without dependency diff"]
        return ["could not prove update is non-major without dependency diff"]

    diff_pairs = version_pairs_from_diff(diff_text)
    known_non_major = False
    for name, before, after in diff_pairs:
        result = pair_is_major(before, after)
        if result is True:
            return [
                f"major-version update detected for {name}: {before} -> {after}"
            ]
        if result is False:
            known_non_major = True

    if known_non_major:
        return []

    if any(is_dependency_file(path) for path in changed_paths(pr)):
        return ["could not determine update level from dependency diff"]

    return []


def commit_authors_are_dependabot(pr: dict[str, Any]) -> tuple[bool, list[str]]:
    commits = pr.get("commits") or []
    if not commits:
        return False, ["missing head commit metadata"]

    reasons: list[str] = []
    for commit in commits:
        oid = (commit.get("oid") or "unknown")[:12]
        authors = commit.get("authors") or []
        if not authors:
            reasons.append(f"commit {oid} has no author metadata")
            continue
        for author in authors:
            login = author.get("login")
            email = author.get("email") or ""
            name = author.get("name") or ""
            if (
                login not in DEPENDABOT_AUTHORS
                and "dependabot[bot]" not in email
                and name != "dependabot[bot]"
            ):
                reasons.append(
                    f"commit {oid} author is {login or email or name!r}"
                )

    return not reasons, reasons


def base_guard_reasons(pr: dict[str, Any]) -> list[str]:
    reasons: list[str] = []

    author = (pr.get("author") or {}).get("login")
    if author not in DEPENDABOT_AUTHORS:
        reasons.append(f"author is {author!r}, not Dependabot")

    if pr.get("state") != "OPEN":
        reasons.append(f"PR state is {pr.get('state')!r}, not OPEN")

    if pr.get("isDraft"):
        reasons.append("PR is draft")

    paths = changed_paths(pr)
    disallowed = [path for path in paths if not is_dependency_file(path)]
    if disallowed:
        reasons.append("non-dependency files changed: " + ", ".join(disallowed))

    commits_ok, commit_reasons = commit_authors_are_dependabot(pr)
    if not commits_ok:
        reasons.extend(commit_reasons)

    return reasons


def classify(
    pr: dict[str, Any],
    *,
    allow_major: bool = False,
    diff_text: str | None = None,
) -> tuple[str, list[str]]:
    reasons = base_guard_reasons(pr)

    mergeable = pr.get("mergeable")
    merge_state = pr.get("mergeStateStatus")
    if mergeable != "MERGEABLE" or merge_state != "CLEAN":
        reasons.append(
            "mergeability must be MERGEABLE/CLEAN; "
            f"got {mergeable}/{merge_state}"
        )

    green, blockers = checks_green(pr)
    if not green:
        reasons.extend(blockers)

    if not allow_major:
        reasons.extend(major_update_reasons(pr, diff_text=diff_text))

    if not reasons:
        return "ready", []

    if any(
        "pending" in reason
        or "QUEUED" in reason
        or "IN_PROGRESS" in reason
        or "UNKNOWN" in reason
        or "UNSTABLE" in reason
        for reason in reasons
    ):
        return "wait", reasons

    if any("missing checks" in reason for reason in reasons):
        return "wait", reasons

    return "manual", reasons


def dependency_dirs(paths: list[str]) -> set[Path]:
    dirs: set[Path] = set()
    for path in paths:
        p = Path(path)
        if p.name in {
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "requirements.txt",
        } or p.name.startswith("requirements"):
            dirs.add(p.parent if str(p.parent) != "." else Path("."))
    return dirs


def pyproject_extras(pyproject: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:
        text = pyproject.read_text(errors="ignore")
        in_optional = False
        found: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line == "[project.optional-dependencies]":
                in_optional = True
                continue
            if in_optional and line.startswith("["):
                break
            if in_optional and "=" in line:
                found.append(line.split("=", 1)[0].strip())
        if "dev" in found:
            return "dev"
        return ",".join(name for name in ("test", "lint") if name in found)

    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception:
        return ""

    optional = (data.get("project") or {}).get("optional-dependencies") or {}
    if "dev" in optional:
        return "dev"

    extras = [name for name in ("test", "lint") if name in optional]
    return ",".join(extras)


def python_bin() -> str:
    return shutil.which("python3.12") or shutil.which("python3") or sys.executable


def verify_python_dir(worktree: Path, rel_dir: Path) -> None:
    package_dir = worktree / rel_dir
    venv = package_dir / ".venv-depbot"
    if venv.exists():
        shutil.rmtree(venv)

    py = python_bin()
    run([py, "-m", "venv", ".venv-depbot"], cwd=package_dir)
    venv_python = venv / "bin" / "python"
    if not venv_python.exists():
        venv_python = venv / "Scripts" / "python.exe"

    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "wheel"], cwd=package_dir)

    pyproject = package_dir / "pyproject.toml"
    if pyproject.exists():
        extras = pyproject_extras(pyproject)
        target = f".[{extras}]" if extras else "."
        run([str(venv_python), "-m", "pip", "install", "-e", target], cwd=package_dir)
    else:
        reqs = sorted(package_dir.glob("requirements*.txt"))
        for req in reqs:
            run([str(venv_python), "-m", "pip", "install", "-r", req.name], cwd=package_dir)

    run([str(venv_python), "-m", "pip", "check"], cwd=package_dir)
    run([str(venv_python), "-m", "compileall", "-q", "."], cwd=package_dir)


def npm_scripts(package_json: Path) -> dict[str, str]:
    try:
        data = json.loads(package_json.read_text())
    except Exception:
        return {}
    return data.get("scripts") or {}


def verify_npm_dir(worktree: Path, rel_dir: Path) -> None:
    package_dir = worktree / rel_dir
    run(["npm", "ci", "--ignore-scripts", "--prefer-offline"], cwd=package_dir)
    scripts = npm_scripts(package_dir / "package.json")
    for script in ("lint", "type-check", "test:ci"):
        if script in scripts:
            run(["npm", "run", script], cwd=package_dir)


def fetch_pr_ref(root: Path, repo: str, number: int) -> str:
    ref = f"refs/remotes/origin/dependabot-maintainer-pr-{number}"
    run(
        [
            "git",
            "fetch",
            "origin",
            f"+pull/{number}/head:{ref}",
        ],
        cwd=root,
    )
    return ref


def create_pr_worktree(root: Path, ref: str) -> Path:
    tempdir = Path(tempfile.mkdtemp(prefix="portfolio-depbot-verify-"))
    run(["git", "worktree", "add", "--detach", str(tempdir), ref], cwd=root)
    return tempdir


def command_report(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or repo_full_name(root)
    prs = pr_list(root, repo, args.limit)
    if not prs:
        print("No open Dependabot PRs.")
        return 0

    for listed in prs:
        pr = pr_view(root, repo, listed["number"])
        diff_text = pr_diff(root, repo, pr["number"])
        status, reasons = classify(
            pr,
            allow_major=args.allow_major,
            diff_text=diff_text,
        )
        paths = changed_paths(pr)
        print(f"## PR #{pr['number']}: {pr['title']}")
        print(f"URL: {pr['url']}")
        print(f"Head: {pr['headRefOid']}")
        print(f"Status: {status}")
        print("Files:")
        for path in paths:
            print(f"- {path}")
        if reasons:
            print("Reasons:")
            for reason in reasons:
                print(f"- {reason}")
        print()
    return 0


def command_guard(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or repo_full_name(root)
    pr = pr_view(root, repo, args.pr_number)
    diff_text = pr_diff(root, repo, args.pr_number)
    status, reasons = classify(
        pr,
        allow_major=args.allow_major,
        diff_text=diff_text,
    )
    print(f"PR #{pr['number']} status: {status}")
    for reason in reasons:
        print(f"- {reason}")
    return 0 if status == "ready" else 1


def command_rebase(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or repo_full_name(root)
    pr = pr_view(root, repo, args.pr_number)
    reasons = base_guard_reasons(pr)
    if reasons:
        print(f"Refusing to rebase PR #{args.pr_number}", file=sys.stderr)
        for reason in reasons:
            print(f"- {reason}", file=sys.stderr)
        return 1
    run(
        [
            "gh",
            "pr",
            "comment",
            str(args.pr_number),
            "--repo",
            repo,
            "--body",
            "@dependabot rebase",
        ],
        cwd=root,
    )
    return 0


def command_verify(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or repo_full_name(root)
    pr = pr_view(root, repo, args.pr_number)
    diff_text = pr_diff(root, repo, args.pr_number)
    reasons = base_guard_reasons(pr)
    if not args.allow_major:
        reasons.extend(major_update_reasons(pr, diff_text=diff_text))
    if reasons:
        print(f"Refusing to verify PR #{args.pr_number}", file=sys.stderr)
        for reason in reasons:
            print(f"- {reason}", file=sys.stderr)
        return 1

    paths = changed_paths(pr)
    dirs = dependency_dirs(paths)

    if not dirs:
        print("No local dependency directories detected. Rely on CI for this PR.")
        return 0

    ref = fetch_pr_ref(root, repo, args.pr_number)
    worktree = create_pr_worktree(root, ref)
    print(f"Created verification worktree: {worktree}")
    try:
        for rel_dir in sorted(dirs):
            full_dir = worktree / rel_dir
            if (full_dir / "package.json").exists():
                verify_npm_dir(worktree, rel_dir)
            elif (full_dir / "pyproject.toml").exists() or list(full_dir.glob("requirements*.txt")):
                verify_python_dir(worktree, rel_dir)
            else:
                print(f"Skipping {rel_dir}: no supported dependency manifest")
    finally:
        if args.keep_worktree:
            print(f"Kept verification worktree: {worktree}")
        else:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False)
            shutil.rmtree(worktree, ignore_errors=True)
    return 0


def command_merge(args: argparse.Namespace) -> int:
    root = repo_root()
    repo = args.repo or repo_full_name(root)
    pr = pr_view(root, repo, args.pr_number)
    diff_text = pr_diff(root, repo, args.pr_number)
    status, reasons = classify(
        pr,
        allow_major=args.allow_major,
        diff_text=diff_text,
    )
    if status != "ready":
        print(f"Refusing to merge PR #{args.pr_number}: {status}", file=sys.stderr)
        for reason in reasons:
            print(f"- {reason}", file=sys.stderr)
        return 1

    if not args.yes:
        print("Refusing to merge without --yes.", file=sys.stderr)
        return 1

    subject = args.subject or pr["title"].split(" from ", 1)[0]
    body = args.body or "Merged by Dependabot Maintainer after guardrails and CI passed."
    run(
        [
            "gh",
            "pr",
            "merge",
            str(args.pr_number),
            "--repo",
            repo,
            "--squash",
            "--delete-branch",
            "--match-head-commit",
            pr["headRefOid"],
            "--subject",
            subject,
            "--body",
            body,
        ],
        cwd=root,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub repo in owner/name form")

    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="List open Dependabot PRs and guardrail status")
    report.add_argument("--limit", type=int, default=30)
    report.add_argument("--allow-major", action="store_true")
    report.set_defaults(func=command_report)

    guard = subparsers.add_parser("guard", help="Return success only when a PR is safe to merge")
    guard.add_argument("pr_number", type=int)
    guard.add_argument("--allow-major", action="store_true")
    guard.set_defaults(func=command_guard)

    verify = subparsers.add_parser("verify", help="Run local dependency checks for a PR")
    verify.add_argument("pr_number", type=int)
    verify.add_argument("--keep-worktree", action="store_true")
    verify.add_argument("--allow-major", action="store_true")
    verify.set_defaults(func=command_verify)

    rebase = subparsers.add_parser("rebase", help="Ask Dependabot to rebase a PR")
    rebase.add_argument("pr_number", type=int)
    rebase.set_defaults(func=command_rebase)

    merge = subparsers.add_parser("merge", help="Squash merge a ready Dependabot PR")
    merge.add_argument("pr_number", type=int)
    merge.add_argument("--allow-major", action="store_true")
    merge.add_argument("--yes", action="store_true")
    merge.add_argument("--subject")
    merge.add_argument("--body")
    merge.set_defaults(func=command_merge)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
