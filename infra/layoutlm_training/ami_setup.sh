#!/bin/bash
# ami_setup.sh - Script to configure the LayoutLM training AMI
# This runs once during AMI creation to pre-install all dependencies
set -euxo pipefail

# Log all output
exec > /var/log/ami-setup.log 2>&1

echo "=== Starting LayoutLM AMI Setup ==="
date

# Ensure LD_LIBRARY_PATH is set for conda
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"

# Activate the PyTorch conda environment from Deep Learning AMI
set +u
source /opt/conda/bin/activate pytorch
set -u

echo "=== Python version ==="
python --version

echo "=== Upgrading pip ==="
python -m pip install --upgrade pip setuptools wheel

# Prefer prebuilt wheels to avoid compiling heavy deps
export PIP_ONLY_BINARY=:all:

echo "=== Installing core dependencies ==="
python -m pip install -U pyarrow==16.1.0 sentencepiece==0.1.99

echo "=== Installing ML dependencies ==="
python -m pip install -U \
    'transformers>=4.40.0' \
    'datasets>=2.19.0' \
    'boto3>=1.34.0' \
    'pillow' \
    'tqdm' \
    'filelock' \
    'pydantic>=2.10.6' \
    'accelerate>=0.21.0' \
    'seqeval' \
    'backports.tarfile'

echo "=== Pre-downloading LayoutLM model weights ==="
python -c "
from transformers import LayoutLMTokenizerFast, LayoutLMForTokenClassification
print('Downloading tokenizer...')
LayoutLMTokenizerFast.from_pretrained('microsoft/layoutlm-base-uncased')
print('Downloading model...')
LayoutLMForTokenClassification.from_pretrained('microsoft/layoutlm-base-uncased')
print('Model weights cached successfully!')
"

echo "=== Setting up environment variables ==="
cat > /etc/profile.d/layoutlm.sh << 'EOF'
# LayoutLM training environment variables
export TORCH_ALLOW_TF32=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export CUDA_DEVICE_MAX_CONNECTIONS=1
export HF_DATASETS_DISABLE_MULTIPROCESSING=1
EOF
chmod 0755 /etc/profile.d/layoutlm.sh

echo "=== Creating wheels directory ==="
mkdir -p /opt/wheels
chmod 0775 /opt/wheels

echo "=== Cleaning up pip cache ==="
python -m pip cache purge || true

echo "=== AMI Setup Complete ==="
date
