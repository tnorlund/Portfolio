"""Tests for the GitHub Actions Dependabot maintenance orchestration.

Every ``gh`` invocation is intercepted at ``dependabot_maintainer.run`` so
the guardrails in ``classify`` run for real against canned GitHub JSON.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / ".agents/skills/dependabot-maintainer/scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))
import dependabot_maintainer as maintainer  # noqa: E402
import dependabot_maintenance_ci as ci  # noqa: E402

REPO = "tnorlund/Portfolio"
ROOT = Path("/repo")
DEPENDABOT_OID = "a" * 40
READY_DIFF = """\
diff --git a/portfolio/package.json b/portfolio/package.json
--- a/portfolio/package.json
+++ b/portfolio/package.json
@@ -1,3 +1,3 @@
-    "left-pad": "1.2.3",
+    "left-pad": "1.2.4",
"""
MAJOR_DIFF = """\
diff --git a/infra/pyproject.toml b/infra/pyproject.toml
--- a/infra/pyproject.toml
+++ b/infra/pyproject.toml
@@ -1,3 +1,3 @@
-    "pulumi>=3.0,<4.0",
+    "pulumi>=4.0,<5.0",
"""
GREEN_CHECKS = [
    {
        "__typename": "CheckRun",
        "name": "CI/CD Pipeline",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
    }
]


def make_pr(
    number: int,
    title: str,
    *,
    files: list[str],
    mergeable: str = "MERGEABLE",
    merge_state: str = "CLEAN",
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "author": {"login": "app/dependabot"},
        "baseRefName": "main",
        "body": "",
        "commits": [{"oid": DEPENDABOT_OID}],
        "files": [{"path": path} for path in files],
        "headRefName": f"dependabot/npm_and_yarn/portfolio/dep-{number}",
        "headRefOid": f"{number:040x}",
        "isDraft": False,
        "mergeStateStatus": merge_state,
        "mergeable": mergeable,
        "number": number,
        "state": "OPEN",
        "statusCheckRollup": GREEN_CHECKS if checks is None else checks,
        "title": title,
        "url": f"https://github.com/{REPO}/pull/{number}",
    }


class FakeGH:
    """Route ``gh`` commands to canned responses and record writes."""

    def __init__(
        self,
        prs: dict[int, dict[str, Any]],
        diffs: dict[int, str],
    ) -> None:
        self.prs = prs
        self.diffs = diffs
        self.commands: list[list[str]] = []
        self.merge_error: int | None = None
        self.main_sha = "initial-main"
        self.bad_release = False
        self.events: list[str] = []
        self.on_view: dict[int, list[dict[str, Any]]] = {}

    @property
    def writes(self) -> list[list[str]]:
        return [
            cmd
            for cmd in self.commands
            if cmd[:3] in (["gh", "pr", "merge"], ["gh", "pr", "comment"])
        ]

    def _view(self, number: int, fields: str) -> dict[str, Any]:
        if fields == "mergeCommit":
            return {"mergeCommit": {"oid": f"merge{number:035d}"}}
        queue = self.on_view.get(number)
        if queue:
            return queue.pop(0)
        return self.prs[number]

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        assert cwd == ROOT
        payload: Any
        if cmd[:3] == ["gh", "pr", "list"]:
            payload = [
                {
                    key: pr[key]
                    for key in (
                        "number",
                        "title",
                        "url",
                        "headRefOid",
                        "mergeable",
                        "mergeStateStatus",
                    )
                }
                for pr in self.prs.values()
            ]
        elif cmd[:3] == ["gh", "pr", "view"]:
            payload = self._view(int(cmd[3]), cmd[cmd.index("--json") + 1])
        elif cmd[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=self.diffs[int(cmd[3])], stderr=""
            )
        elif cmd[:2] == ["gh", "api"] and cmd[2].endswith("/commits/main"):
            payload = {"sha": self.main_sha}
        elif cmd[:2] == ["gh", "api"] and "/actions/runs?" in cmd[2]:
            sha = cmd[2].split("head_sha=")[1].split("&")[0]
            payload = {
                "workflow_runs": [
                    {
                        "id": 123,
                        "path": ".github/workflows/main.yml",
                        "event": "push",
                        "head_branch": "main",
                        "head_sha": sha,
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://example.test/run",
                    }
                ]
            }
        elif cmd[:2] == ["gh", "api"] and "/jobs?" in cmd[2]:
            self.events.append("release:" + self.main_sha)
            payload = {
                "jobs": [
                    {
                        "name": name,
                        "conclusion": (
                            "failure"
                            if self.bad_release
                            and self.main_sha != "initial-main"
                            else "success"
                        ),
                    }
                    for name in ("Deploy", "Smoke Tests")
                ]
            }
        elif cmd[:2] == ["gh", "api"] and "/commits/" in cmd[2]:
            payload = {
                "author": {"login": "dependabot[bot]"},
                "committer": {"login": "dependabot[bot]"},
                "commit": {
                    "author": {"name": "dependabot[bot]"},
                    "committer": {"name": "dependabot[bot]"},
                },
            }
        elif cmd[:3] == ["gh", "pr", "merge"]:
            if self.merge_error is not None:
                raise subprocess.CalledProcessError(self.merge_error, cmd)
            self.main_sha = f"merge{int(cmd[3]):035d}"
            self.events.append("merge:" + cmd[3])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        elif cmd[:3] == ["gh", "pr", "comment"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        else:
            raise AssertionError(f"unexpected command: {cmd}")
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload), stderr=""
        )


@pytest.fixture(name="gh")
def gh_fixture(monkeypatch: pytest.MonkeyPatch) -> FakeGH:
    prs = {
        1551: make_pr(
            1551,
            "chore(deps): bump left-pad from 1.2.3 to 1.2.4 in /portfolio",
            files=["portfolio/package.json", "portfolio/package-lock.json"],
        ),
        1552: make_pr(
            1552,
            "chore(deps): update pulumi requirement from <4.0 to <5.0",
            files=["infra/pyproject.toml"],
        ),
        1553: make_pr(
            1553,
            "chore(deps): bump left-pad from 1.2.3 to 1.2.4 in /scripts",
            files=["scripts/package.json", "scripts/README.md"],
            mergeable="CONFLICTING",
            merge_state="DIRTY",
        ),
        1554: make_pr(
            1554,
            "chore(deps): bump left-pad from 1.2.3 to 1.2.4 in /infra",
            files=["infra/package.json"],
            mergeable="CONFLICTING",
            merge_state="DIRTY",
        ),
    }
    diffs = {
        1551: READY_DIFF,
        1552: MAJOR_DIFF,
        1553: READY_DIFF.replace("portfolio/", "scripts/"),
        1554: READY_DIFF.replace("portfolio/", "infra/"),
    }
    fake = FakeGH(prs, diffs)
    monkeypatch.setattr(maintainer, "run", fake)
    monkeypatch.setattr(
        ci,
        "verify_candidate",
        lambda root, repo, number, **kwargs: fake.events.append(
            f"verify:{number}"
        ),
    )
    return fake


def run_ci(gh: FakeGH, **overrides: Any) -> ci.Outcome:
    kwargs: dict[str, Any] = {
        "mode": "merge",
        "allow_major": False,
        "dry_run": False,
        "limit": 50,
        "retries": 2,
        "retry_delay": 0,
    }
    kwargs.update(overrides)
    del gh  # the fixture is only needed for its monkeypatch side effect
    return ci.run_maintenance(ROOT, REPO, **kwargs)


def test_select_ready_returns_only_ready_prs_in_number_order() -> None:
    records = [
        ci.PRRecord(3, "c", "u", "h", "ready"),
        ci.PRRecord(2, "b", "u", "h", "wait", ["CI: pending"]),
        ci.PRRecord(1, "a", "u", "h", "ready"),
        ci.PRRecord(4, "d", "u", "h", "manual", ["major"]),
    ]
    assert [r.number for r in ci.select_ready(records)] == [1, 3]


def test_report_mode_classifies_without_writing(gh: FakeGH) -> None:
    outcome = run_ci(gh, mode="report")

    statuses = {r.number: r.status for r in outcome.records}
    assert statuses == {
        1551: "ready",
        1552: "manual",
        1553: "manual",
        1554: "manual",
    }
    major = next(r for r in outcome.records if r.number == 1552)
    assert any("major-version" in reason for reason in major.reasons)
    assert gh.writes == []
    assert outcome.merged == []
    assert outcome.exit_code == 0


def test_dry_run_reports_intended_merges_without_calling_gh(
    gh: FakeGH, capsys: pytest.CaptureFixture[str]
) -> None:
    outcome = run_ci(gh, dry_run=True)

    assert gh.writes == []
    assert [(m.number, m.merge_sha) for m in outcome.merged] == [(1551, None)]
    assert outcome.rebase_requested == [1554]
    assert outcome.rebase_refused == [
        (1553, ["non-dependency files changed: scripts/README.md"])
    ]
    out = capsys.readouterr().out
    assert "DRY RUN: would run: gh pr merge 1551" in out
    assert "DRY RUN: would run: gh pr comment 1554" in out

    summary = ci.format_summary(outcome)
    assert "# Dependabot maintenance (merge, dry-run)" in summary
    assert "## Would merge" in summary
    assert "(dry run)" in summary
    assert "## Would request rebase" in summary


def test_merge_mode_merges_ready_and_requests_rebases(gh: FakeGH) -> None:
    outcome = run_ci(gh)

    merges = [cmd for cmd in gh.writes if cmd[2] == "merge"]
    assert len(merges) == 1
    merge_cmd = merges[0]
    assert merge_cmd[3] == "1551"
    assert "--squash" in merge_cmd
    assert "--match-head-commit" in merge_cmd
    assert merge_cmd[merge_cmd.index("--match-head-commit") + 1] == (
        f"{1551:040x}"
    )
    assert merge_cmd[merge_cmd.index("--subject") + 1] == (
        "chore(deps): bump left-pad"
    )

    comments = [cmd for cmd in gh.writes if cmd[2] == "comment"]
    assert [(cmd[3], cmd[-1]) for cmd in comments] == [
        ("1554", "@dependabot rebase")
    ]

    assert [(m.number, m.merge_sha) for m in outcome.merged] == [
        (1551, f"merge{1551:035d}")
    ]
    summary = ci.format_summary(outcome)
    assert f"`merge{1551:035d}`" in summary
    assert "## Rebase requested" in summary
    assert "[#1554]" in summary
    assert outcome.exit_code == 0


def test_guard_recheck_skips_pr_that_stopped_being_ready(gh: FakeGH) -> None:
    conflicted = dict(gh.prs[1551])
    conflicted.update(mergeable="CONFLICTING", mergeStateStatus="DIRTY")
    # First view feeds the report; the second (pre-merge guard) is stale.
    gh.on_view[1551] = [gh.prs[1551], conflicted]

    outcome = run_ci(gh)

    assert outcome.merged == []
    assert [r.number for r in outcome.skipped] == [1551]
    assert all(cmd[2] != "merge" for cmd in gh.writes)
    assert outcome.rebase_requested == [1554]
    assert outcome.exit_code == 0


def test_guard_retries_while_mergeability_is_unknown(gh: FakeGH) -> None:
    unknown = dict(gh.prs[1551])
    unknown.update(mergeable="UNKNOWN", mergeStateStatus="UNKNOWN")
    gh.on_view[1551] = [gh.prs[1551], unknown, gh.prs[1551]]

    outcome = run_ci(gh, retries=2)

    assert [m.number for m in outcome.merged] == [1551]
    views = [cmd for cmd in gh.commands if cmd[:3] == ["gh", "pr", "view"]]
    assert sum(1 for cmd in views if cmd[3] == "1551") >= 3


def test_merge_failure_stops_and_sets_exit_code(gh: FakeGH) -> None:
    second_ready = make_pr(
        1560,
        "chore(deps): bump left-pad from 1.2.3 to 1.2.4 in /tools",
        files=["tools/package.json"],
    )
    gh.prs[1560] = second_ready
    gh.diffs[1560] = READY_DIFF.replace("portfolio/", "tools/")
    gh.merge_error = 1

    outcome = run_ci(gh)

    assert outcome.merged == []
    assert outcome.failure is not None
    assert "PR #1551" in outcome.failure
    assert outcome.not_attempted == [1560]
    assert outcome.rebase_requested == []
    assert all(cmd[2] != "comment" for cmd in gh.writes)
    assert outcome.exit_code == 1
    assert "**Failure:**" in ci.format_summary(outcome)


def test_main_writes_summary_file(
    gh: FakeGH, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del gh
    monkeypatch.setattr(maintainer, "repo_root", lambda: ROOT)
    summary = tmp_path / "summary.md"

    exit_code = ci.main(
        [
            "--repo",
            REPO,
            "--mode",
            "merge",
            "--dry-run",
            "--guard-retry-delay",
            "0",
            "--summary-file",
            str(summary),
        ]
    )

    assert exit_code == 0
    text = summary.read_text()
    assert "# Dependabot maintenance (merge, dry-run)" in text
    assert "[#1551]" in text
    assert "### manual (3)" in text


def add_second_ready(gh):
    gh.prs[1560] = make_pr(
        1560, "chore(deps): patch", files=["tools/package.json"]
    )
    gh.diffs[1560] = READY_DIFF.replace("portfolio/", "tools/")


def test_next_merge_waits_for_previous_deployment(gh):
    add_second_ready(gh)
    outcome = run_ci(gh)
    assert outcome.exit_code == 0
    first_merge = gh.events.index("merge:1551")
    first_release = gh.events.index(f"release:merge{1551:035d}", first_merge)
    second_merge = gh.events.index("merge:1560")
    assert (
        gh.events.index("verify:1551")
        < first_merge
        < first_release
        < second_merge
    )
    assert all(result.release_run == 123 for result in outcome.merged)


def test_bad_deployment_stops_and_preserves_landed_merge(gh):
    add_second_ready(gh)
    gh.bad_release = True
    outcome = run_ci(gh)
    assert outcome.exit_code == 1
    assert [result.number for result in outcome.merged] == [1551]
    assert outcome.merged[0].release_run is None
    assert outcome.not_attempted == [1560]
    assert outcome.rebase_requested == []
    assert "merge:1560" not in gh.events


def test_verification_failure_stops_without_merging(gh, monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["verify"])

    monkeypatch.setattr(ci, "verify_candidate", failed)
    outcome = run_ci(gh)
    assert outcome.exit_code == 1
    assert gh.writes == []


def test_head_changed_after_verification_is_not_merged(gh, monkeypatch):
    def changed(*args, **kwargs):
        gh.prs[1551] = {**gh.prs[1551], "headRefOid": "b" * 40}

    monkeypatch.setattr(ci, "verify_candidate", changed)
    outcome = run_ci(gh)
    assert "head or checks changed" in outcome.failure
    assert gh.writes == []


def test_main_changed_during_verification_is_not_merged(gh, monkeypatch):
    monkeypatch.setattr(
        ci,
        "verify_candidate",
        lambda *args, **kwargs: setattr(gh, "main_sha", "another-release"),
    )
    outcome = run_ci(gh)
    assert "Main advanced" in outcome.failure
    assert gh.writes == []


def test_merge_limit_leaves_rest_open(gh):
    add_second_ready(gh)
    outcome = run_ci(gh, max_merges=1)
    assert outcome.exit_code == 0
    assert [result.number for result in outcome.merged] == [1551]
    assert outcome.not_attempted == [1560]


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [{"name": "Deploy", "conclusion": "success"}],
        [
            {"name": "Deploy", "conclusion": "skipped"},
            {"name": "Smoke Tests", "conclusion": "success"},
        ],
    ],
)
def test_release_requires_both_successful_jobs(gh, monkeypatch, jobs):
    original = maintainer.run_json

    def read(cmd, **kwargs):
        if "/jobs?" in cmd[2]:
            return {"jobs": jobs}
        return original(cmd, **kwargs)

    monkeypatch.setattr(maintainer, "run_json", read)
    with pytest.raises(RuntimeError, match="lacks successful"):
        ci.wait_for_main_release(ROOT, REPO, "initial-main")


def test_release_ignores_wrong_sha_branch_and_workflow(gh, monkeypatch):
    ticks = iter([0, 0, 100])
    monkeypatch.setattr(ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(ci.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        maintainer,
        "run_json",
        lambda *args, **kwargs: {
            "workflow_runs": [
                {
                    "id": 123,
                    "path": ".github/workflows/other.yml",
                    "event": "pull_request",
                    "head_branch": "feature",
                    "head_sha": "wrong",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
    )
    with pytest.raises(TimeoutError, match="did not complete"):
        ci.wait_for_main_release(ROOT, REPO, "initial-main", timeout=30)


def test_verifier_receives_only_read_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-write-token")
    monkeypatch.setenv("GITHUB_TOKEN", "test-also-write")
    monkeypatch.setenv("VERIFICATION_GH_TOKEN", "test-read-token")
    captured = {}

    def run(cmd, **kwargs):
        captured.update(kwargs)
        assert cmd[-2:] == ["verify", "1551"]

    monkeypatch.setattr(subprocess, "run", run)
    ci.verify_candidate(ROOT, REPO, 1551, allow_major=False)
    assert captured["env"]["GH_TOKEN"] == "test-read-token"
    assert "GITHUB_TOKEN" not in captured["env"]
    assert "VERIFICATION_GH_TOKEN" not in captured["env"]
    assert "test-write-token" not in captured["env"].values()


def test_non_main_pr_cannot_enter_merge_queue(gh):
    gh.prs[1551]["baseRefName"] = "release"
    outcome = run_ci(gh)
    record = next(item for item in outcome.records if item.number == 1551)
    assert record.status == "manual"
    assert "PR must target main" in record.reasons
    assert all(cmd[2] != "merge" for cmd in gh.writes)


def test_skipped_candidate_summary_uses_latest_status(gh):
    stale = {
        **gh.prs[1551],
        "mergeable": "CONFLICTING",
        "mergeStateStatus": "DIRTY",
    }
    gh.on_view[1551] = [gh.prs[1551], stale]
    outcome = run_ci(gh)
    assert (
        next(item for item in outcome.records if item.number == 1551).status
        != "ready"
    )
    assert "### ready" not in ci.format_summary(outcome)


def test_batch_major_override_is_rejected(gh):
    with pytest.raises(ValueError, match="individually reviewed"):
        run_ci(gh, allow_major=True)
    assert gh.writes == []


def test_final_summary_goes_to_stdout_and_step_summary(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    final = "Merged #1; release 123 passed; #2 was not attempted.\n"
    ci.write_summary(final, str(summary))
    assert summary.read_text() == final
    assert final in capsys.readouterr().out
