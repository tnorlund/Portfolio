import sys
from pathlib import Path

import pytest

# Add container_ocr to sys.path so 'handler' resolves without
# traversing up through infra/upload_images/__init__.py (which
# imports Pulumi infrastructure and fails outside Pulumi context).
_container_ocr = str(Path(__file__).resolve().parents[2])
if _container_ocr not in sys.path:
    sys.path.insert(0, _container_ocr)


@pytest.fixture(autouse=True)
def _stub_native_embedding_write(monkeypatch):
    """Chroma teardown: re-OCR ALWAYS rewrites the receipt's native
    DynamoDB embeddings (batched OpenAI embed + engine writer + sweep).
    Stub it so these unit tests stay offline; failure-path tests can
    re-patch it per test."""
    import receipt_upload.merchant_resolution.dynamo_embedding_write as dew

    monkeypatch.setattr(
        dew,
        "write_native_embeddings",
        lambda *args, **kwargs: {
            "requests": 0,
            "written": 0,
            "skipped_existing": 0,
            "failed": 0,
            "swept": 0,
        },
    )
