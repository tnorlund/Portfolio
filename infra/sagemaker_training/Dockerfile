# LayoutLM Training Container for SageMaker
# Based on PyTorch with CUDA support

FROM pytorch/pytorch:2.2.0-cuda12.1-cudnn8-runtime

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

# Install Python dependencies
COPY infra/sagemaker_training/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the training packages (entire package directories including pyproject.toml)
COPY receipt_dynamo/ /opt/ml/code/receipt_dynamo/
COPY receipt_layoutlm/ /opt/ml/code/receipt_layoutlm/

# Install the packages (non-editable for production)
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
