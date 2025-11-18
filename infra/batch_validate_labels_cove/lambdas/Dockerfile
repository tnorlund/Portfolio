# syntax=docker/dockerfile:1
# Multi-stage build for Batch Validate Labels CoVe Lambda

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Install dependencies (no ChromaDB needed for this Lambda - just LangChain/Ollama)
# Note: [lambda] extra includes chromadb-client, but base dependencies include langchain-ollama
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir "/tmp/receipt_label[lambda]" && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the validate receipt handler
COPY infra/batch_validate_labels_cove/lambdas/validate_receipt_cove_templates.py ${LAMBDA_TASK_ROOT}/

# Set the handler
CMD ["validate_receipt_cove_templates.lambda_handler"]

