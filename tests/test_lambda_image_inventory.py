"""Ensure every upgraded production image receives the real import check."""

import json
import re
from pathlib import Path

from scripts.lambda_image_import_check import IMAGES

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
