"""Ensure every upgraded production image receives the real import check."""

import json
import re
import subprocess
from pathlib import Path

from scripts.lambda_image_import_check import IMAGES, build_needed

ROOT = Path(__file__).resolve().parents[1]


def test_all_python314_lambda_images_are_in_the_build_matrix():
    upgraded = {
        str(path.relative_to(ROOT))
        for path in (ROOT / "infra").rglob("Dockerfile")
        if re.search(
            r"^FROM public\.ecr\.aws/lambda/python:3\.14(?:\s|$)",
            path.read_text(),
            re.M,
        )
    }
    assert upgraded == {image[1] for image in IMAGES}
    assert len(upgraded) == 12


def test_import_checks_match_the_deployed_handler_commands():
    for _, dockerfile, handler, _ in IMAGES:
        cmd = re.search(
            r"^CMD\s+(\[.*\])", (ROOT / dockerfile).read_text(), re.M
        )
        assert cmd is not None
        assert json.loads(cmd[1]) == [handler]


def test_layoutlm_keeps_the_torch_compatible_runtime():
    dockerfile = (
        ROOT
        / "infra/routes/layoutlm_inference_cache_generator/lambdas/Dockerfile"
    )
    assert "FROM public.ecr.aws/lambda/python:3.13" in dockerfile.read_text()


def test_build_plan_includes_earlier_commits_in_a_push(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", *args], cwd=tmp_path, text=True
        ).strip()

    git("init", "-q")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "CI")
    (tmp_path / "Dockerfile").write_text("FROM example:old\n")
    git("add", ".")
    git("commit", "-qm", "initial")
    before = git("rev-parse", "HEAD")
    (tmp_path / "Dockerfile").write_text("FROM example:new\n")
    git("commit", "-qam", "runtime")
    (tmp_path / "README.md").write_text("Updated documentation\n")
    git("add", ".")
    git("commit", "-qm", "docs")
    assert build_needed(tmp_path, "push", before)
    assert not build_needed(tmp_path, "push", git("rev-parse", "HEAD^1"))
    assert build_needed(tmp_path, "push", "0" * 40)
    assert build_needed(tmp_path, "workflow_dispatch")
