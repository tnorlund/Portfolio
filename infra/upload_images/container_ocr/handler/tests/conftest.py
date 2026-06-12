import sys
from pathlib import Path

# Add container_ocr to sys.path so 'handler' resolves without
# traversing up through infra/upload_images/__init__.py (which
# imports Pulumi infrastructure and fails outside Pulumi context).
_container_ocr = str(Path(__file__).resolve().parents[2])
if _container_ocr not in sys.path:
    sys.path.insert(0, _container_ocr)
