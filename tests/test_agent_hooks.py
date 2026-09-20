"""Offline command/payload contracts; proposed commands are never executed."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts/agent-hooks/guard-shell.py"
SPEC = importlib.util.spec_from_file_location("agent_guard", HOOK)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)
FORMAT_HOOK = ROOT / "scripts/agent-hooks/format-python.py"
FORMAT_SPEC = importlib.util.spec_from_file_location(
    "agent_formatter", FORMAT_HOOK
)
formatter = importlib.util.module_from_spec(FORMAT_SPEC)
FORMAT_SPEC.loader.exec_module(formatter)


@pytest.mark.parametrize(
    "command",
    [
        "pulumi cancel --stack tnorlund/portfolio/dev",
        "cd infra&&pulumi up --stack tnorlund/portfolio/prod",
        "echo ok;pulumi up --stack tnorlund/portfolio/prod",
        "echo ok\npulumi up --stack tnorlund/portfolio/prod",
        "echo ok;\npulumi up --stack tnorlund/portfolio/prod",
        "env -i pulumi up --stack tnorlund/portfolio/prod",
        "env -u EXAMPLE pulumi up --stack tnorlund/portfolio/prod",
        "env -S 'pulumi up --stack tnorlund/portfolio/prod'",
        "sudo -u root pulumi up --stack tnorlund/portfolio/prod",
        "sudo --user=root env -i pulumi up --stack tnorlund/portfolio/prod",
        "nohup pulumi up",
        "git -C repo push --force origin branch",
        "git -c user.name=test push --force origin branch",
        "git push --force-with-lease=refs/heads/main:abc origin branch",
        "git push -qf origin branch",
        "git push origin HEAD:refs/heads/main",
        "git push origin :refs/heads/main",
        "git push --all origin",
        "git push --mirror origin",
        "git push origin refs/heads/*:refs/heads/*",
        "aws --profile prod dynamodb delete-table --table-name ReceiptsTable-d7ff76a",
        "aws --region us-east-1 --profile=prod dynamodb update-item --table-name=ReceiptsTable-d7ff76a",
        "aws dynamodb batch-write-item --request-items '{\"ReceiptsTable-d7ff76a\":[]}'",
        "python -u scripts/copy_dynamodb_dev_to_prod.py",
        "python3 -X dev scripts/copy_dynamodb_dev_to_prod.py",
        "bash -x scripts/start_ingestion_prod.sh",
        "python3 scripts/check.py --live=true",
    ],
)
def test_common_protected_forms_are_denied(command):
    assert guard.evaluate(command, str(ROOT))


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git -C repo log -n 1",
        "git push -u origin codex/example",
        "pulumi preview --stack tnorlund/portfolio/dev",
        "aws --profile dev dynamodb describe-table --table-name ReceiptsTable-dc5be22",
        "python3 -u scripts/check.py --dry-run",
        "echo 'pulumi up --stack tnorlund/portfolio/prod'",
    ],
)
def test_benign_direct_commands_are_allowed(command):
    assert guard.evaluate(command, str(ROOT)) is None


@pytest.mark.parametrize("form", ["cd", "git-C", "git-dir", "env-C", "sudo-D"])
def test_commit_checks_effective_worktree(tmp_path, form):
    main = tmp_path / "main-checkout"
    feature = tmp_path / "feature-checkout"
    for path, branch in ((main, "main"), (feature, "codex/test")):
        subprocess.run(
            ["git", "init", "-q", "-b", branch, str(path)], check=True
        )
    commands = {
        "cd": f"cd {main}&&git commit -am example",
        "git-C": f"git -C {main} commit -am example",
        "git-dir": f"git --git-dir={main / '.git'} commit -am example",
        "env-C": f"env -C {main} git commit -am example",
        "sudo-D": f"sudo -D {main} git commit -am example",
    }
    assert guard.evaluate(commands[form], str(feature))


@pytest.mark.parametrize("harness", ["cursor", "claude", "codex"])
def test_denial_payload_contract(harness):
    command = "pulumi cancel --stack tnorlund/portfolio/dev"
    payload = {"cwd": str(ROOT)}
    if harness == "cursor":
        payload["command"] = command
    else:
        payload.update(tool_name="Bash", tool_input={"command": command})
        if harness == "codex":
            payload["turn_id"] = "offline-protocol-check"
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    output = json.loads(result.stdout)
    if harness == "cursor":
        assert output["permission"] == "deny"
    else:
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("payload", ["not json", "[]", "null", "{}"])
@pytest.mark.parametrize("script", [HOOK, FORMAT_HOOK])
def test_malformed_payload_does_not_crash(payload, script):
    subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"file_path": "changed.py"},
        {"tool_input": {"file_path": "changed.py"}},
        {
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: changed.py\n+x=1\n*** End Patch"
            },
        },
        {
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: old.py\n*** Move to: changed.py\n*** End Patch"
            },
        },
    ],
)
def test_formatter_recognizes_documented_edits(payload):
    assert "changed.py" in formatter._candidate_paths(payload)


def test_formatter_timeout_does_not_fail_the_edit(tmp_path, monkeypatch):
    source = tmp_path / "changed.py"
    source.write_text("x=1\n")
    monkeypatch.setattr(formatter, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(formatter, "_formatter", lambda name: name)
    monkeypatch.setattr(
        formatter.sys,
        "stdin",
        io.StringIO(json.dumps({"file_path": str(source)})),
    )

    def timeout(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 60)

    monkeypatch.setattr(formatter.subprocess, "run", timeout)
    assert formatter.main() == 0


def test_formatter_refuses_files_outside_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "private.py"
    outside.write_text("x=1\n")
    monkeypatch.setattr(formatter, "REPO_ROOT", checkout)
    assert formatter._eligible(str(outside), checkout) is None
