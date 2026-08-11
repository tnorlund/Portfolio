#!/usr/bin/env bash

set -euo pipefail

# Bootstrap for Cursor cloud-agent VMs (Ubuntu).
# Mirrors the "repository-tests" install block in .github/workflows/main.yml:
# Python 3.13 venv with every local package editable-installed, plus the
# Next.js app's node_modules. scripts/ensure_python_runtime.sh is
# Homebrew-based (self-hosted Mac runners) and does not work here.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_VERSION="3.13"

# --- Python 3.13 (matches the CI pin) ---
if command -v "python${PYTHON_VERSION}" >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v "python${PYTHON_VERSION}")"
else
    if ! command -v uv >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    uv python install "$PYTHON_VERSION"
    PYTHON_BIN="$(uv python find "$PYTHON_VERSION")"
fi
"$PYTHON_BIN" --version

if [[ ! -x .venv/bin/python ]]; then
    "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade --quiet pip wheel
pip install -e receipt_dynamo
pip install --no-deps -e receipt_dynamo_stream
pip install --no-deps -e receipt_chroma
pip install --no-deps -e receipt_places
pip install --no-deps -e receipt_agent
pip install --no-deps -e receipt_upload
pip install boto3 chromadb "openai>=2.8.1,<3.0.0" Pillow \
    pillow-avif-plugin langsmith langgraph \
    "langchain-core>=0.3.0" "langchain-openai>=0.2.0" httpx \
    pydantic pydantic-settings structlog requests tenacity \
    segno python-barcode numpy
pip install pytest pytest-asyncio pytest-mock pytest-cov pytest-xdist \
    pytest-timeout pytest-rerunfailures moto responses
pip install black isort

# --- Node 22 (matches CI) + Next.js app dependencies ---
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    source "$NVM_DIR/nvm.sh"
    nvm install 22
    nvm alias default 22
    nvm use 22
fi
node --version
(cd portfolio && npm ci --prefer-offline)

echo "Install complete: source .venv/bin/activate for the Python stack."
