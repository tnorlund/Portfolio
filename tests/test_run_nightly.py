"""End-to-end wrapper tests for scripts/nightly/run_nightly.sh (W2, plan
humble-skipping-quilt): --dry-run must produce a contract-passing report,
and a preflight RED must produce the RED stub without ever launching the
agent. No claude call, no network push: preflight is faked, dry-run skips
git, and the RED test publishes inside a throwaway git repo whose origin
is a local bare repo (exercising commit + --force-with-lease push and the
same-date re-run).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.nightly import check_contract

REPO_ROOT = Path(__file__).parent.parent
WRAPPER = REPO_ROOT / "scripts" / "nightly" / "run_nightly.sh"


def write_executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture()
def nightly_env(tmp_path: Path) -> dict[str, str]:
    """Hermetic env: fake healthy preflight, tmp report/log/campaign paths."""
    preflight = write_executable(
        tmp_path / "fake_preflight_ok.sh",
        '#!/bin/bash\necho \'{"overall":"healthy","checks":{}}\'\n'
        'echo "== agent preflight: healthy ==" >&2\nexit 0\n',
    )
    env = dict(os.environ)
    env.update(
        {
            "NIGHTLY_DATE": "1999-01-01",
            "NIGHTLY_REPORT_DIR": str(tmp_path / "reports"),
            "NIGHTLY_LOG_DIR": str(tmp_path / "logs"),
            "NIGHTLY_CAMPAIGN_LOG": str(tmp_path / "CAMPAIGN_LOG.md"),
            "NIGHTLY_PREFLIGHT_BIN": str(preflight),
        }
    )
    return env


def run_wrapper(
    env: dict[str, str], *args: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(WRAPPER), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_dry_run_produces_contract_passing_report(nightly_env, tmp_path):
    # Seed a stale per-date run dir; the retention sweep must remove it.
    import time as _time

    stale = tmp_path / "logs" / "1998-12-01"
    stale.mkdir(parents=True)
    old = _time.time() - 20 * 86400
    os.utime(stale, (old, old))

    proc = run_wrapper(nightly_env, "--dry-run")
    assert proc.returncode == 0, proc.stderr

    assert not stale.exists(), "14-day log retention sweep did not run"

    report = tmp_path / "reports" / "1999-01-01.md"
    assert report.exists(), "wrapper must always produce a report"

    result = check_contract.check_report_text(report.read_text())
    assert result["valid"] is True, result["problems"]
    assert result["verdict"] == "YELLOW"  # canned dry-run output is YELLOW
    assert "1999-01-01" in report.read_text()  # {{DATE}} substituted

    # Publish leg: no git mutation, but the campaign log line is appended.
    campaign = (tmp_path / "CAMPAIGN_LOG.md").read_text()
    assert "nightly-v0 1999-01-01 verdict=YELLOW" in campaign
    wrapper_log = (
        tmp_path / "logs" / "1999-01-01" / "wrapper.log"
    ).read_text()
    assert "[dry-run] would commit" in wrapper_log


def test_preflight_red_produces_red_stub(nightly_env, tmp_path):
    # Preflight exits 2 (RED); the agent must never be launched.
    write_executable(
        Path(nightly_env["NIGHTLY_PREFLIGHT_BIN"]),
        '#!/bin/bash\necho \'{"overall":"red","checks":{}}\'\n'
        'echo "== agent preflight: red ==" >&2\nexit 2\n',
    )
    sentinel = tmp_path / "claude_was_called"
    fake_claude = write_executable(
        tmp_path / "fake_claude.sh",
        f'#!/bin/bash\ntouch "{sentinel}"\nexit 0\n',
    )
    # Publish runs for real (no --dry-run) inside a throwaway repo whose
    # origin is a local bare repo: the commit AND push legs are exercised.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    stub_repo = tmp_path / "stub_repo"
    stub_repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "root",
        ],
        ["git", "remote", "add", "origin", str(origin)],
    ):
        subprocess.run(cmd, cwd=stub_repo, check=True)

    env = dict(nightly_env)
    env.update(
        {
            "NIGHTLY_DATE": "1999-01-02",
            "NIGHTLY_REPO_ROOT": str(stub_repo),
            "CLAUDE_BIN": str(fake_claude),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
    )
    proc = run_wrapper(env)
    assert proc.returncode == 0, proc.stderr

    assert not sentinel.exists(), "claude must not run on preflight RED"

    report = tmp_path / "reports" / "1999-01-02.md"
    assert report.exists(), "RED stub must still be produced"
    text = report.read_text()
    result = check_contract.check_report_text(text)
    assert result["valid"] is True, result["problems"]
    assert result["verdict"] == "RED"
    assert "preflight RED" in text

    # The stub was committed on the nightly/ branch AND pushed to origin.
    def origin_sha() -> str:
        return subprocess.run(
            ["git", "rev-parse", "refs/heads/nightly/1999-01-02"],
            cwd=origin,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    first_sha = origin_sha()
    assert first_sha

    campaign = (tmp_path / "CAMPAIGN_LOG.md").read_text()
    assert "nightly-v0 1999-01-02 verdict=RED" in campaign

    # Same-date re-run: --force-with-lease must land the new commit on
    # origin (last-run-wins; the branch is never merged). Distinct commit
    # timestamps make the SHAs comparable (stub content is deterministic).
    import time as _time

    _time.sleep(1.1)
    env["GIT_COMMITTER_DATE"] = "2000-01-01T00:00:00Z"
    env["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00Z"
    proc2 = run_wrapper(env)
    assert proc2.returncode == 0, proc2.stderr
    second_sha = origin_sha()
    assert second_sha != first_sha, "re-run did not reach origin"


def test_invalid_agent_report_replaced_by_red_stub(nightly_env, tmp_path):
    """A claude run that writes garbage must end in a valid RED stub."""
    bad_claude = write_executable(
        tmp_path / "bad_claude.sh",
        '#!/bin/bash\necho "not a report" > "$NIGHTLY_REPORT_PATH"\nexit 0\n',
    )
    stub_repo = tmp_path / "stub_repo2"
    stub_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=stub_repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "root",
        ],
        cwd=stub_repo,
        check=True,
    )
    # The wrapper reads the brief from the repo root; give the stub repo one.
    brief_dir = stub_repo / "docs" / "nightly"
    brief_dir.mkdir(parents=True)
    (brief_dir / "BRIEF.md").write_text("test brief\n")

    env = dict(nightly_env)
    env.update(
        {
            "NIGHTLY_DATE": "1999-01-03",
            "NIGHTLY_REPO_ROOT": str(stub_repo),
            "CLAUDE_BIN": str(bad_claude),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
    )
    proc = run_wrapper(env)
    assert proc.returncode == 0, proc.stderr

    report = tmp_path / "reports" / "1999-01-03.md"
    result = check_contract.check_report_text(report.read_text())
    assert result["valid"] is True, result["problems"]
    assert result["verdict"] == "RED"
    assert "failed contract check" in report.read_text()
    # The rejected original is preserved for debugging.
    rejected = tmp_path / "logs" / "1999-01-03" / "rejected_report.md"
    assert rejected.read_text() == "not a report\n"
