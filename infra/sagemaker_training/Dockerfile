# LayoutLM Training Container for SageMaker
# Based on PyTorch with CUDA support.
#
# Why torch >= 2.6: transformers 5.x has TWO torch version guards:
#   1. Import-level: requires torch >= 2.4 to enable PyTorch backends at
#      all (otherwise LayoutLMForTokenClassification raises
#      "PyTorch library not found"). Fixed initially by PR #894 with
#      torch 2.5.1.
#   2. Checkpoint-load: torch.load(weights_only=True) refuses to run on
#      torch < 2.6 due to a CVE. transformers raises a ValueError when
#      HuggingFace Trainer tries to resume from a checkpoint. This broke
#      v7-may2026 (continuation from v6 via --resume-from-s3, PR #890)
#      and isn't catchable by the Dockerfile's pre-download step since
#      that step only exercises from_pretrained, not torch.load.
# torch 2.6 satisfies both guards; receipt_layoutlm/pyproject.toml caps
# at <2.8 for forward safety.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # SageMaker paths
    SAGEMAKER_PROGRAM=train.py \
    # Performance optimizations
    TORCH_ALLOW_TF32=1 \
    TOKENIZERS_PARALLELISM=false \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    HF_DATASETS_DISABLE_MULTIPROCESSING=1

WORKDIR /opt/ml/code

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the training packages (entire package directories including pyproject.toml)
COPY receipt_dynamo/ /opt/ml/code/receipt_dynamo/
COPY receipt_layoutlm/ /opt/ml/code/receipt_layoutlm/

# Install the packages — receipt_layoutlm/pyproject.toml is the single source
# of truth for all dependencies (including accelerate, datasets, etc.)
RUN pip install --no-cache-dir /opt/ml/code/receipt_dynamo && \
    pip install --no-cache-dir /opt/ml/code/receipt_layoutlm

# Pre-download model weights to speed up training start
RUN python -c "\
from transformers import LayoutLMTokenizerFast, LayoutLMForTokenClassification; \
LayoutLMTokenizerFast.from_pretrained('microsoft/layoutlm-base-uncased'); \
LayoutLMForTokenClassification.from_pretrained('microsoft/layoutlm-base-uncased')"

# Copy training entrypoint
COPY infra/sagemaker_training/train.py /opt/ml/code/train.py

# SageMaker expects the training script at SAGEMAKER_PROGRAM
ENTRYPOINT ["python", "/opt/ml/code/train.py"]
