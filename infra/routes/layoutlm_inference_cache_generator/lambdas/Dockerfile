# syntax=docker/dockerfile:1
# Multi-stage build for LayoutLM Inference Cache Generator Lambda

# Stage 1: Build and install dependencies (cached layer)
# Runs on Python 3.14 like every other container Lambda. torch publishes
# cp314 CPU wheels from 2.10 onward; receipt_layoutlm only caps torch <3.0
# because the coremltools/Core AI ceilings live in the [coreml]/[coreai]
# extras that the isolated macOS export workers install.
FROM public.ecr.aws/lambda/python:3.14 AS dependencies

# Note: awscli is pre-installed in the Lambda base image, no need to install it

# Install CPU-only PyTorch first (Lambda has no GPU, saves ~1.5GB)
RUN pip install --no-cache-dir \
    torch==2.14.0 \
    --index-url https://download.pytorch.org/whl/cpu

# Copy dependency packages
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_layoutlm/ /tmp/receipt_layoutlm/

# Install packages — receipt_layoutlm/pyproject.toml is the single source
# of truth for all dependencies. pip sees torch is already satisfied.
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_layoutlm && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_layoutlm

# Stage 2: Copy Lambda-specific code
FROM dependencies

# Copy the cache generator handler module
COPY infra/routes/layoutlm_inference_cache_generator/lambdas/index.py ${LAMBDA_TASK_ROOT}/index.py

# Set the handler
CMD ["index.handler"]
