"""Finding H: baked JSON assets under extra_context_paths must feed the image
content hash, and the hash for callers with NO extra_context_paths must be
byte-identical to the pre-fix behavior.

The build context rsync copies the entire extra_context_paths tree (font.json,
stylemap.json, glyphs/*.json), but the image content hash filtered to
python/project files only -- so a font-only edit would not rebuild the image.
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
)
import infra.components.codebuild_docker_image as cdi  # noqa: E402
from infra.shared.build_utils import compute_hash  # noqa: E402


def _bare_instance(**attrs):
    """A CodeBuildDockerImage with only the attrs _calculate_content_hash reads,
    bypassing __init__ (which provisions pulumi resources)."""
    obj = object.__new__(cdi.CodeBuildDockerImage)
    obj.dockerfile_path = attrs["dockerfile_path"]
    obj.build_context_path = attrs.get("build_context_path", ".")
    obj.source_paths = attrs.get("source_paths")
    obj.extra_context_paths = attrs.get("extra_context_paths", [])
    return obj


def _make_project(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    extra = tmp_path / "fonts" / "sprouts"
    (extra / "glyphs").mkdir(parents=True)
    (extra / "font.json").write_text('{"params": {"weight": 1.0}}')
    (extra / "glyphs" / "u0046.json").write_text('{"codepoint": 70}')
    return tmp_path


def test_json_edit_under_extra_context_changes_hash(tmp_path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.setattr(cdi, "PROJECT_DIR", str(tmp_path))

    inst = _bare_instance(
        dockerfile_path="Dockerfile",
        extra_context_paths=["fonts/sprouts"],
    )
    before = inst._calculate_content_hash()

    # Editing a baked font JSON must now change the image content hash.
    (tmp_path / "fonts" / "sprouts" / "font.json").write_text(
        '{"params": {"weight": 1.33}}'
    )
    after = inst._calculate_content_hash()
    assert before != after

    # Editing a nested glyph JSON also triggers it.
    (tmp_path / "fonts" / "sprouts" / "glyphs" / "u0046.json").write_text(
        '{"codepoint": 70, "overrides": {"weight": 1.2}}'
    )
    after2 = inst._calculate_content_hash()
    assert after2 != after


def test_no_extra_context_hash_unchanged(tmp_path, monkeypatch):
    """Callers with no extra_context_paths keep the exact pre-fix digest."""
    _make_project(tmp_path)
    monkeypatch.setattr(cdi, "PROJECT_DIR", str(tmp_path))

    inst = _bare_instance(dockerfile_path="Dockerfile", extra_context_paths=[])
    # Pre-fix behavior == compute_hash over the same roots with the same globs
    # and NO extra_strings. The Dockerfile parent (".") handler dir has no .py.
    baseline = compute_hash(
        [tmp_path / "Dockerfile"],
        include_globs=[
            "**/*.py",
            "**/pyproject.toml",
            "**/requirements.txt",
            "Dockerfile",
        ],
    )
    assert inst._calculate_content_hash() == baseline

    # And a font JSON edit elsewhere in the tree does NOT affect a no-extra
    # caller (it never references those paths).
    (tmp_path / "fonts" / "sprouts" / "font.json").write_text('{"x": 9}')
    assert inst._calculate_content_hash() == baseline


def test_extra_strings_none_is_noop():
    """The mechanism relied on: extra_strings=None must not perturb the hash."""
    roots = [os.path.dirname(os.path.abspath(__file__))]
    globs = ["**/*.py"]
    assert compute_hash(roots, include_globs=globs) == compute_hash(
        roots, include_globs=globs, extra_strings=None
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
