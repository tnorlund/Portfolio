# Multi-stage build for Validate Metadata Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies with full ChromaDB support
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir "/tmp/receipt_label[full]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the validate metadata handler and utils
COPY infra/validate_metadata/lambdas/validate_metadata_handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY infra/validate_metadata/lambdas/utils/ ${LAMBDA_TASK_ROOT}/utils/

# Set the handler
CMD ["handler.lambda_handler"]

