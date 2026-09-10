"""Load the local server and the Lambda files selected by its Dockerfile."""

import importlib.util
import shlex
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
LAMBDA_DOCKERFILE = REPO_ROOT / "infra/mcp_server_lambda/lambdas/Dockerfile"
SERVER_FILES = {
    "stdio": REPO_ROOT / "scripts/receipt_mcp_server.py",
    "lambda": LAMBDA_DOCKERFILE,
}


def stage_lambda_files(destination: Path) -> None:
    """Copy runtime files exactly where the Dockerfile places them."""
    for line in LAMBDA_DOCKERFILE.read_text().splitlines():
        if not line.startswith("COPY "):
            continue
        _, source, target = shlex.split(line)
        prefix = "${LAMBDA_TASK_ROOT}/"
        if target.startswith(prefix):
            output = destination / target.removeprefix(prefix)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / source, output)


def load_server_module(name: str, source: Path) -> ModuleType:
    """Import a fresh local server or the staged Lambda server."""
    with TemporaryDirectory(prefix="receipt-mcp-test-") as directory:
        if source == LAMBDA_DOCKERFILE:
            stage_lambda_files(Path(directory))
            source = Path(directory) / "receipt_mcp_server/server.py"
        spec = importlib.util.spec_from_file_location(name, source)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
