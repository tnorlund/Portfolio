# LayoutLM Training Container for SageMaker
# Based on PyTorch with CUDA support.
#
# Why torch >= 2.4: transformers >= 5.0 (see PR #859) requires PyTorch
# 2.4+. With torch 2.2 the library logs "Disabling PyTorch because
# PyTorch >= 2.4 is required but found 2.2.0" at import time, after
# which LayoutLMForTokenClassification.from_pretrained() fails with
# "PyTorch library not found." The Dockerfile's pre-download step
# trips this, breaking every container build.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

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
