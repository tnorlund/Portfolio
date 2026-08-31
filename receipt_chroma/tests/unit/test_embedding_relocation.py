"""Compatibility contracts for the receipt_embeddings relocation."""

from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from pathlib import Path

import pytest

MODULE_PAIRS = [
    (
        "receipt_chroma.embedding.formatting",
        "receipt_embeddings.formatting",
    ),
    (
        "receipt_chroma.embedding.formatting.line_format",
        "receipt_embeddings.formatting.line_format",
    ),
    (
        "receipt_chroma.embedding.formatting.receipt_rows",
        "receipt_embeddings.formatting.receipt_rows",
    ),
    (
        "receipt_chroma.embedding.formatting.word_format",
        "receipt_embeddings.formatting.word_format",
    ),
    ("receipt_chroma.embedding.openai", "receipt_embeddings.openai"),
    (
        "receipt_chroma.embedding.openai.batch_status",
        "receipt_embeddings.openai.batch_status",
    ),
    (
        "receipt_chroma.embedding.openai.helpers",
        "receipt_embeddings.openai.helpers",
    ),
    (
        "receipt_chroma.embedding.openai.poll",
        "receipt_embeddings.openai.poll",
    ),
    (
        "receipt_chroma.embedding.openai.realtime",
        "receipt_embeddings.openai.realtime",
    ),
    (
        "receipt_chroma.embedding.openai.submit",
        "receipt_embeddings.openai.submit",
    ),
]


@pytest.mark.parametrize(("old_path", "new_path"), MODULE_PAIRS)
def test_shim_public_objects_are_identical(
    old_path: str, new_path: str
) -> None:
    """Every supported old-path object is the relocated object itself."""

    old_module = importlib.import_module(old_path)
    new_module = importlib.import_module(new_path)

    assert old_module.__all__
    if hasattr(new_module, "__all__"):
        assert old_module.__all__ == new_module.__all__
    for name in old_module.__all__:
        assert getattr(old_module, name) is getattr(new_module, name), name


def test_all_repository_old_path_imports_still_resolve() -> None:
    """Pin every checked-in import site to the compatibility surface."""

    repository_root = Path(__file__).resolve().parents[3]
    failures: list[str] = []

    for path in repository_root.rglob("*.py"):
        relative_path = path.relative_to(repository_root)
        if any(part.startswith(".venv") for part in relative_path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith(
                (
                    "receipt_chroma.embedding.formatting",
                    "receipt_chroma.embedding.openai",
                )
            ):
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                if alias.name != "*" and not hasattr(module, alias.name):
                    failures.append(
                        f"{relative_path}:{node.lineno} "
                        f"missing {node.module}.{alias.name}"
                    )

    assert not failures, ", ".join(failures)


@dataclass(frozen=True)
class _Line:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]

    def calculate_centroid(self) -> tuple[float, float]:
        box = self.bounding_box
        return (
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
        )


def test_old_and_new_formatting_outputs_are_byte_identical() -> None:
    """A fixed formatting input produces identical encoded output bytes."""

    old_module = importlib.import_module(
        "receipt_chroma.embedding.formatting.line_format"
    )
    new_module = importlib.import_module(
        "receipt_embeddings.formatting.line_format"
    )
    lines = [
        _Line(
            "image",
            1,
            1,
            "APPLES",
            {"x": 0.1, "y": 0.8, "width": 0.4, "height": 0.1},
        ),
        _Line(
            "image",
            1,
            2,
            "$4.99",
            {"x": 0.8, "y": 0.8, "width": 0.1, "height": 0.1},
        ),
        _Line(
            "image",
            1,
            3,
            "TOTAL $4.99",
            {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.1},
        ),
    ]

    old_bytes = repr(old_module.get_row_embedding_inputs(lines)).encode()
    new_bytes = repr(new_module.get_row_embedding_inputs(lines)).encode()

    assert old_bytes == new_bytes
