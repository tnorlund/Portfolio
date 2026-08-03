#!/usr/bin/env bash
# Promote a review session out of the gitignored .dev-harness/ and into the
# repo, where a verdict survives the laptop it was made on.
#
#   portfolio/dev-harness/sync_reviews.sh [date]
#
# Appends only entries the target file does not already have, keyed on
# image_id/receipt_id/ts, so running it twice in one session is a no-op.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE="${VALIDATION_REVIEW_LOG:-${REPO_ROOT}/.dev-harness/review_log.jsonl}"
DATE="${1:-$(date -u +%Y-%m-%d)}"
TARGET="${REPO_ROOT}/docs/line-items/reviews/${DATE}.jsonl"

if [[ ! -f "${SOURCE}" ]]; then
  echo "no review log at ${SOURCE} — nothing to sync" >&2
  exit 1
fi

mkdir -p "$(dirname "${TARGET}")"
SOURCE="${SOURCE}" TARGET="${TARGET}" python3 - <<'PY'
import json
import os
import pathlib

source = pathlib.Path(os.environ["SOURCE"])
target = pathlib.Path(os.environ["TARGET"])


def load(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def key(entry):
    return (entry.get("image_id"), entry.get("receipt_id"), entry.get("ts"))


existing = load(target)
seen = {key(entry) for entry in existing}
added = [entry for entry in load(source) if key(entry) not in seen]

with target.open("a", encoding="utf-8") as handle:
    for entry in added:
        handle.write(json.dumps(entry) + "\n")

print(f"{len(added)} new verdict(s) -> {target} ({len(existing) + len(added)} total)")
PY
