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
# torch 2.6 satisfies both guards; receipt_layoutlm/pyproject.toml keeps
# training on the CoreML-validated 2.6-2.7 line. The upstream image currently
# ships Python 3.11, so create an explicit Python 3.13 environment and install
# the matching official CUDA wheel instead of using the base interpreter.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime AS training-runtime

ARG PYTHON_VERSION=3.13
ARG PYTORCH_VERSION=2.6.0
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu124

ENV PYTHON_ENV=/opt/python313

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

# Keep the CUDA runtime from the proven PyTorch image, but do not reuse its
# Python 3.11 environment. PyTorch publishes CPython 3.13 wheels for 2.6.0 on
# the CUDA 12.4 index, matching the CUDA runtime in the base image.
RUN /opt/conda/bin/conda create --yes --prefix "${PYTHON_ENV}" \
        "python=${PYTHON_VERSION}" pip wheel \
    && /opt/conda/bin/conda clean --all --yes

ENV PATH="${PYTHON_ENV}/bin:${PATH}"

RUN python -m pip install --no-cache-dir \
        "torch==${PYTORCH_VERSION}" \
        --index-url "${PYTORCH_INDEX_URL}" \
    && python -c "import sys, torch; \
assert sys.version_info[:2] == (3, 13), sys.version; \
assert torch.__version__.startswith('2.6.0'), torch.__version__; \
assert torch.version.cuda == '12.4', torch.version.cuda"

# Copy the training packages (entire package directories including pyproject.toml)
COPY receipt_dynamo/ /opt/ml/code/receipt_dynamo/
COPY receipt_layoutlm/ /opt/ml/code/receipt_layoutlm/

# Install the packages — receipt_layoutlm/pyproject.toml is the single source
# of truth for all dependencies (including accelerate, datasets, etc.)
RUN python -m pip install --no-cache-dir /opt/ml/code/receipt_dynamo && \
    python -m pip install --no-cache-dir \
        "/opt/ml/code/receipt_layoutlm[training]" \
    && python -m pip check \
    && python -c "import receipt_dynamo, receipt_layoutlm, seqeval, sklearn, torch"

# Keep dependency validation independently buildable without downloading both
# pretrained checkpoints. CodeBuild builds the final stage by default; local
# and CI checks can stop at training-runtime after exercising package install.
FROM training-runtime AS final

# Pre-download model weights to speed up training start
RUN python -c "\
from transformers import LayoutLMTokenizerFast, LayoutLMForTokenClassification; \
LayoutLMTokenizerFast.from_pretrained('microsoft/layoutlm-base-uncased'); \
LayoutLMForTokenClassification.from_pretrained('microsoft/layoutlm-base-uncased')"

# Pre-download v3 model weights + image processor
RUN python -c "\
from transformers import LayoutLMv3TokenizerFast, LayoutLMv3ForTokenClassification, LayoutLMv3ImageProcessor; \
LayoutLMv3TokenizerFast.from_pretrained('microsoft/layoutlmv3-base'); \
LayoutLMv3ForTokenClassification.from_pretrained('microsoft/layoutlmv3-base'); \
LayoutLMv3ImageProcessor.from_pretrained('microsoft/layoutlmv3-base')"

# Copy training entrypoint
COPY infra/sagemaker_training/train.py /opt/ml/code/train.py

# SageMaker expects the training script at SAGEMAKER_PROGRAM
ENTRYPOINT ["python", "/opt/ml/code/train.py"]
