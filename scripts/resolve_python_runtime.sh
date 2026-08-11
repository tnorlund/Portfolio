#!/usr/bin/env bash

set -euo pipefail

required_minor="${1:?usage: resolve_python_runtime.sh <major.minor>}"

candidates=(
    "/Library/Frameworks/Python.framework/Versions/${required_minor}/bin/python${required_minor}"
    "/Library/Frameworks/Python.framework/Versions/${required_minor}/bin/python3"
    "/opt/homebrew/bin/python${required_minor}"
    "/usr/local/bin/python${required_minor}"
)

path_candidate="$(command -v "python${required_minor}" 2>/dev/null || true)"
if [[ -n "$path_candidate" ]]; then
    candidates+=("$path_candidate")
fi

for candidate in "${candidates[@]}"; do
    if [[ ! -x "$candidate" ]]; then
        continue
    fi

    actual_minor="$($candidate -c \
        'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [[ "$actual_minor" == "$required_minor" ]]; then
        printf '%s\n' "$candidate"
        exit 0
    fi
done

printf 'Python %s not found in the framework, Homebrew, or PATH\n' \
    "$required_minor" >&2
exit 1
