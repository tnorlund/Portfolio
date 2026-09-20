"""A damaged, incomplete or ambiguous backup must never pass verification."""

# pytest fixture arguments intentionally share their fixture's name.
# pylint: disable=redefined-outer-name

import json
import os
import stat
from pathlib import Path

import pytest

from scripts import backup_receipt_exports as backup


@pytest.fixture
def exports(tmp_path: Path) -> Path:
    """Include the still, metadata and Live Photo companion in one batch."""
    source = tmp_path / "exports"
    source.mkdir()
    (source / "receipt.heic").write_bytes(b"unmodified-original")
    (source / "receipt.xmp").write_bytes(b"exported-metadata")
    (source / "live").mkdir()
    (source / "live" / "receipt.mov").write_bytes(b"live-photo-companion")
    return source


def test_backup_is_restorable_without_sources(
    exports: Path, tmp_path: Path
) -> None:
    """Verification depends on the archive, not the working export."""
    destination = tmp_path / "backup"
    manifest = backup.create_backup(exports, destination)
    for entry in manifest["files"]:
        original = exports / entry["path"]
        assert (
            destination / "files" / entry["path"]
        ).read_bytes() == original.read_bytes()
        original.unlink()
    assert backup.verify_backup(destination) == manifest


def test_existing_backup_is_never_overwritten(
    exports: Path, tmp_path: Path
) -> None:
    """A rerun with changed input must preserve the original backup."""
    destination = tmp_path / "backup"
    backup.create_backup(exports, destination)
    before = (destination / "manifest.json").read_bytes()
    (exports / "receipt.heic").write_bytes(b"different-photo")
    with pytest.raises(FileExistsError):
        backup.create_backup(exports, destination)
    assert (destination / "manifest.json").read_bytes() == before
    backup.verify_backup(destination)


def test_backup_is_private_with_permissive_umask(
    exports: Path, tmp_path: Path
) -> None:
    """Exported originals and their manifest never inherit public access."""
    nested = exports / "year" / "month"
    nested.mkdir(parents=True)
    (nested / "receipt.heic").write_bytes(b"nested-original")
    destination = tmp_path / "backup"
    previous = os.umask(0)
    try:
        backup.create_backup(exports, destination)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    for path in destination.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


@pytest.mark.parametrize("damage", ["corrupt", "missing", "extra", "symlink"])
def test_invalid_copies_fail(
    exports: Path, tmp_path: Path, damage: str
) -> None:
    """Missing, modified, extra or redirected contents invalidate a copy."""
    destination = tmp_path / "backup"
    backup.create_backup(exports, destination)
    target = destination / "files" / "receipt.heic"
    if damage == "corrupt":
        target.write_bytes(b"corrupted-copy")
    elif damage == "missing":
        target.unlink()
    elif damage == "extra":
        (destination / "files" / "unexpected.jpg").write_bytes(b"extra")
    else:
        target.unlink()
        target.symlink_to(exports / "receipt.heic")
    with pytest.raises(backup.BackupError):
        backup.verify_backup(destination)


@pytest.mark.parametrize("bad_path", ["../receipt.heic", "/tmp/receipt.heic"])
def test_manifest_cannot_escape_backup(
    exports: Path, tmp_path: Path, bad_path: str
) -> None:
    """Manifest filenames cannot redirect verification outside the backup."""
    destination = tmp_path / "backup"
    manifest = backup.create_backup(exports, destination)
    manifest["files"][0]["path"] = bad_path
    (destination / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(backup.BackupError):
        backup.verify_backup(destination)


def test_changed_source_leaves_no_success_manifest(
    exports: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unstable export is not certified, and its original remains intact."""
    destination = tmp_path / "backup"
    real_copy = backup.shutil.copyfileobj

    def copy_then_change_source(reader, writer, **kwargs):
        real_copy(reader, writer, **kwargs)
        (exports / "new.jpg").write_bytes(b"new-photo")

    monkeypatch.setattr(backup.shutil, "copyfileobj", copy_then_change_source)
    with pytest.raises(backup.BackupError, match="changed during backup"):
        backup.create_backup(exports, destination)
    assert not (destination / "manifest.json").exists()
    assert (exports / "receipt.heic").read_bytes() == b"unmodified-original"


def test_nested_destination_and_source_symlinks_refused(
    exports: Path, tmp_path: Path
) -> None:
    """Backups neither recurse into themselves nor follow export links."""
    with pytest.raises(backup.BackupError, match="outside"):
        backup.create_backup(exports, exports / "backup")
    (exports / "unrelated").symlink_to(tmp_path / "somewhere")
    with pytest.raises(backup.BackupError, match="Links"):
        backup.create_backup(exports, tmp_path / "backup")


def test_dangling_destination_symlink_refused(
    exports: Path, tmp_path: Path
) -> None:
    """A destination link cannot redirect new archive creation."""
    destination = tmp_path / "backup"
    redirected = tmp_path / "elsewhere"
    destination.symlink_to(redirected)
    with pytest.raises(backup.BackupError, match="symbolic link"):
        backup.create_backup(exports, destination)
    assert not redirected.exists()
