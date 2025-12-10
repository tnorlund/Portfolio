# syntax=ghcr.io/docker/dockerfile:1.9.0
# Multi-stage build for LayoutLM Inference Cache Generator Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Note: awscli is pre-installed in the Lambda base image, no need to install it

# Copy dependency packages
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_layoutlm/ /tmp/receipt_layoutlm/

# Install dependencies
# Note: PyTorch is large - using CPU-only version for Lambda
# torch==2.5.1+cpu doesn't exist, using 2.6.0+cpu (next available CPU version)
RUN pip install --no-cache-dir \
    torch==2.6.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir \
    transformers>=4.40.0 \
    datasets>=2.19.0 \
    boto3>=1.34.0 \
    pillow tqdm filelock 'pydantic>=2.10.6' \
    'accelerate>=0.21.0' \
    seqeval && \
    pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_layoutlm && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_layoutlm

# Stage 2: Copy Lambda-specific code
FROM dependencies

# Copy the cache generator handler module
COPY infra/routes/layoutlm_inference_cache_generator/lambdas/index.py ${LAMBDA_TASK_ROOT}/index.py

# Set the handler
CMD ["index.handler"]

