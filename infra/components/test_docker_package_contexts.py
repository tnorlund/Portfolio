"""Contracts between Lambda Dockerfiles and their CodeBuild contexts."""

import ast
import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Infrastructure file, Dockerfile, and the zero-based CodeBuildDockerImage call
# within that infrastructure file. The label evaluator owns two image builds.
CHROMA_IMAGE_CONTEXTS = (
    (
        "infra/chromadb_compaction/components/docker_image.py",
        "infra/chromadb_compaction/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/embedding_step_functions/components/docker_image.py",
        "infra/embedding_step_functions/unified_embedding/Dockerfile",
        0,
    ),
    (
        "infra/upload_images/infra.py",
        "infra/upload_images/container_ocr/Dockerfile",
        1,
    ),
    (
        "infra/routes/address_similarity_cache_generator/infra.py",
        "infra/routes/address_similarity_cache_generator/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/routes/word_similarity_cache_generator/infra.py",
        "infra/routes/word_similarity_cache_generator/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/combine_receipts_step_functions/infrastructure.py",
        "infra/combine_receipts_step_functions/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/fix_place_lambda/infrastructure.py",
        "infra/fix_place_lambda/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/mcp_server_lambda/infrastructure.py",
        "infra/mcp_server_lambda/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/merge_receipt_lambda/infrastructure.py",
        "infra/merge_receipt_lambda/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/label_refresh_lambda/infrastructure.py",
        "infra/label_refresh_lambda/lambdas/Dockerfile",
        0,
    ),
    (
        "infra/label_evaluator_step_functions/infrastructure.py",
        "infra/label_evaluator_step_functions/lambdas/Dockerfile.unified",
        0,
    ),
    (
        "infra/label_evaluator_step_functions/infrastructure.py",
        (
            "infra/label_evaluator_step_functions/lambdas/"
            "Dockerfile.unified_pattern_builder"
        ),
        1,
    ),
    (
        "infra/qa_agent_step_functions/infrastructure.py",
        "infra/qa_agent_step_functions/lambdas/Dockerfile",
        0,
    ),
)


def _codebuild_source_path_sets(path: Path) -> list[set[str]]:
    """Return literal ``source_paths`` sets in source order."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else getattr(node.func, "attr", "")
        )
        if function_name != "CodeBuildDockerImage":
            continue
        source_paths = next(
            (
                keyword.value
                for keyword in node.keywords
                if keyword.arg == "source_paths"
            ),
            None,
        )
        if not isinstance(source_paths, (ast.List, ast.Tuple)):
            continue
        values = {
            element.value
            for element in source_paths.elts
            if isinstance(element, ast.Constant)
            and isinstance(element.value, str)
        }
        calls.append((node.lineno, values))
    return [values for _, values in sorted(calls)]


def _dockerfile_package_copies(path: Path) -> set[str]:
    """Return monorepo package roots copied by a Dockerfile."""
    return set(
        re.findall(
            r"^COPY\s+(receipt_[a-z0-9_]+)/",
            path.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    )


def test_chroma_dockerfiles_match_codebuild_source_paths() -> None:
    """Every copied monorepo package must be present in the upload context."""
    for infrastructure, dockerfile, call_index in CHROMA_IMAGE_CONTEXTS:
        source_paths = _codebuild_source_path_sets(
            REPOSITORY_ROOT / infrastructure
        )[call_index]
        source_packages = {
            path for path in source_paths if path.startswith("receipt_")
        }
        copied_packages = _dockerfile_package_copies(
            REPOSITORY_ROOT / dockerfile
        )

        assert source_packages == copied_packages, dockerfile


def test_only_compaction_installs_dynamo_stream() -> None:
    """The stream parser stays out of images that only consume ChromaDB."""
    stream_contexts = {
        dockerfile
        for _, dockerfile, _ in CHROMA_IMAGE_CONTEXTS
        if "receipt_dynamo_stream"
        in _dockerfile_package_copies(REPOSITORY_ROOT / dockerfile)
    }

    assert stream_contexts == {"infra/chromadb_compaction/lambdas/Dockerfile"}


def test_all_chroma_package_dockerfiles_are_covered() -> None:
    """New Chroma image contexts must opt into the package contract."""
    discovered = {
        str(path.relative_to(REPOSITORY_ROOT))
        for path in (REPOSITORY_ROOT / "infra").rglob("Dockerfile*")
        if "COPY receipt_chroma/" in path.read_text(encoding="utf-8")
    }
    declared = {dockerfile for _, dockerfile, _ in CHROMA_IMAGE_CONTEXTS}

    assert discovered == declared
