# syntax=docker/dockerfile:1
# Container for upload_receipt Lambda
# Minimal dependencies - only needs receipt_dynamo and receipt_upload for SQS utils

FROM public.ecr.aws/lambda/python:3.12-arm64 AS dependencies

# Copy only the minimal dependency packages needed
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_dynamo_stream/ /tmp/receipt_dynamo_stream/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/
COPY receipt_upload/ /tmp/receipt_upload/

# Install dependencies in correct order
# receipt_upload depends on receipt_dynamo, receipt_chroma, receipt_places
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_dynamo_stream /tmp/receipt_chroma /tmp/receipt_places /tmp/receipt_agent /tmp/receipt_upload

# Stage 2: Copy Lambda-specific code
FROM dependencies

# Bust cache for handler code changes
ARG CACHE_BUST=unknown
RUN echo "Cache bust: $CACHE_BUST"

# Copy the handler module
COPY infra/upload_images/container_upload/handler/ ${LAMBDA_TASK_ROOT}/handler/

# Set the handler
CMD ["handler.handler.lambda_handler"]
