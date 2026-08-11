#!/usr/bin/env bash

set -euo pipefail

required_minor="${1:?usage: ensure_python_runtime.sh <major.minor>}"
script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
resolver="${PYTHON_RUNTIME_RESOLVER:-${script_directory}/resolve_python_runtime.sh}"
lock_directory="${PYTHON_INSTALL_LOCK_ROOT:-/tmp}/portfolio-python-${required_minor}.lock"
lock_owner_file="${lock_directory}/owner"
poll_seconds="${PYTHON_INSTALL_POLL_SECONDS:-5}"
owns_lock=false

resolve_runtime() {
    "$resolver" "$required_minor" 2>/dev/null
}

release_lock() {
    if [[ "$owns_lock" == true ]]; then
        rm -f "$lock_owner_file"
        rmdir "$lock_directory" 2>/dev/null || true
    fi
}

if runtime="$(resolve_runtime)"; then
    printf '%s\n' "$runtime"
    exit 0
fi

brew_binary="${HOMEBREW_BIN:-}"
if [[ -z "$brew_binary" ]]; then
    for candidate in \
        /opt/homebrew/bin/brew \
        /usr/local/bin/brew \
        "$(command -v brew 2>/dev/null || true)"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            brew_binary="$candidate"
            break
        fi
    done
fi

if [[ -z "$brew_binary" || ! -x "$brew_binary" ]]; then
    printf 'Python %s is unavailable and Homebrew was not found\n' \
        "$required_minor" >&2
    exit 1
fi

for _ in {1..120}; do
    if mkdir "$lock_directory" 2>/dev/null; then
        owns_lock=true
        printf '%s\n' "$$" > "$lock_owner_file"
        trap release_lock EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        break
    fi

    if runtime="$(resolve_runtime)"; then
        printf '%s\n' "$runtime"
        exit 0
    fi

    if [[ -f "$lock_owner_file" ]]; then
        lock_owner="$(<"$lock_owner_file")"
        if [[ "$lock_owner" =~ ^[0-9]+$ ]] \
            && ! kill -0 "$lock_owner" 2>/dev/null; then
            rm -f "$lock_owner_file"
            rmdir "$lock_directory" 2>/dev/null || true
            continue
        fi
    fi

    sleep "$poll_seconds"
done

if [[ "$owns_lock" != true ]]; then
    printf 'Timed out waiting to install Python %s\n' \
        "$required_minor" >&2
    exit 1
fi

# A previous runner may have completed installation while this process was
# acquiring the lock.
if runtime="$(resolve_runtime)"; then
    printf '%s\n' "$runtime"
    exit 0
fi

printf 'Installing Python %s with Homebrew...\n' "$required_minor" >&2
HOMEBREW_NO_AUTO_UPDATE=1 \
HOMEBREW_NO_INSTALL_CLEANUP=1 \
    "$brew_binary" install "python@${required_minor}" >&2

if runtime="$(resolve_runtime)"; then
    printf '%s\n' "$runtime"
    exit 0
fi

printf 'Homebrew completed, but Python %s is still unavailable\n' \
    "$required_minor" >&2
exit 1
