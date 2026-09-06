#!/usr/bin/env python3
"""Copy exported Photos files into a new, independently verifiable backup.

This operates on exported files only. It never changes the Photos library,
uploads files, deletes sources, or overwrites an existing backup directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class BackupError(ValueError):
    """The backup cannot be certified complete and unchanged."""


def _inventory(directory: Path) -> list[Path]:
    """Reject links and special files rather than copying unrelated data."""
    if directory.is_symlink() or not directory.is_dir():
        raise BackupError(f"Expected a regular directory: {directory}")
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise BackupError(f"Links and special files are refused: {path}")
        if path.is_file():
            files.append(path)
    if not files:
        raise BackupError("No exported files found")
    return files


def _fingerprint(path: Path) -> dict[str, str | int]:
    """Read every byte; file length or an S3 ETag alone is insufficient."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    """Verify a backup without requiring its source files to still exist."""
    manifest_path = backup_dir / "manifest.json"
    if backup_dir.is_symlink() or manifest_path.is_symlink():
        raise BackupError("Backup and manifest must not be symbolic links")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise BackupError("Unsupported backup manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise BackupError("Manifest contains no files")
    content_dir = backup_dir / "files"
    actual = {
        path.relative_to(content_dir).as_posix()
        for path in _inventory(content_dir)
    }
    expected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("Invalid manifest entry")
        name = entry.get("path")
        if not isinstance(name, str):
            raise BackupError("Invalid file path")
        relative = PurePosixPath(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or name in expected
            or name not in actual
        ):
            raise BackupError(f"Invalid, duplicate or missing file: {name}")
        expected.add(name)
        fingerprint = _fingerprint(content_dir / name)
        if any(entry.get(key) != value for key, value in fingerprint.items()):
            raise BackupError(f"Checksum or length mismatch: {name}")
    if actual != expected:
        raise BackupError("Backup contains files absent from the manifest")
    return manifest


def create_backup(source_dir: Path, backup_dir: Path) -> dict[str, Any]:
    """Certify a fixed source inventory after copying and a full readback."""
    if backup_dir.is_symlink():
        raise BackupError("Backup destination must not be a symbolic link")
    source_files = _inventory(source_dir)
    source = source_dir.resolve()
    destination = backup_dir.resolve()
    if destination == source or source in destination.parents:
        raise BackupError("Backup must be outside the export directory")
    entries = [
        {"path": path.relative_to(source_dir).as_posix(), **_fingerprint(path)}
        for path in source_files
    ]
    # exist_ok=False prevents reruns from replacing a previously good backup.
    destination.mkdir(parents=True, exist_ok=False)
    content_dir = destination / "files"
    content_dir.mkdir()
    for entry in entries:
        relative = str(entry["path"])
        target = content_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with (source / relative).open("rb") as reader, target.open(
            "xb"
        ) as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _fingerprint(target) != {
            key: entry[key] for key in ("bytes", "sha256")
        }:
            raise BackupError(f"Backup readback failed: {relative}")

    # Source edits/additions during copying invalidate this attempt. Keep
    # incomplete copies for recovery, but do not publish a success manifest.
    current = [
        {"path": path.relative_to(source).as_posix(), **_fingerprint(path)}
        for path in _inventory(source)
    ]
    if current != entries:
        raise BackupError("Export directory changed during backup")
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source),
        "files": entries,
    }
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return verify_backup(destination)


def main() -> int:
    """Create or verify a file backup; never authorize library deletion."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("backup", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            manifest = create_backup(args.source, args.destination)
        else:
            manifest = verify_backup(args.backup)
    except (BackupError, OSError, json.JSONDecodeError) as error:
        print(f"Backup verification failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Verified {len(manifest['files'])} files by SHA-256 and byte count."
    )
    print("This verifies file copies only; it does not authorize deletion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
