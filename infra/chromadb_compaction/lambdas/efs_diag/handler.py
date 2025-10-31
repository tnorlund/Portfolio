import json
import os
from pathlib import Path


def _list_dir(path: Path, limit: int = 20):
    try:
        entries = []
        for p in sorted(path.iterdir(), key=lambda x: x.name, reverse=True)[:limit]:
            try:
                stat = p.stat()
                entries.append({
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": stat.st_size if p.is_file() else None,
                })
            except Exception as e:  # noqa: BLE001
                entries.append({"name": p.name, "error": str(e)})
        return {"exists": True, "entries": entries}
    except FileNotFoundError:
        return {"exists": False, "entries": []}
    except Exception as e:  # noqa: BLE001
        return {"exists": False, "error": str(e), "entries": []}


def lambda_handler(event, context):  # noqa: ARG001
    root = os.environ.get("CHROMA_ROOT", "/mnt/chroma")
    lines = Path(root) / "snapshots" / "lines"
    words = Path(root) / "snapshots" / "words"

    def read_version(dir_path: Path):
        try:
            vf = dir_path / ".version"
            return vf.read_text().strip() if vf.exists() else None
        except Exception:  # noqa: BLE001
            return None

    result = {
        "root": root,
        "lines": {
            "version": read_version(lines),
            "dir": str(lines),
            "list": _list_dir(lines),
        },
        "words": {
            "version": read_version(words),
            "dir": str(words),
            "list": _list_dir(words),
        },
    }

    return {
        "statusCode": 200,
        "body": json.dumps(result, indent=2),
    }


