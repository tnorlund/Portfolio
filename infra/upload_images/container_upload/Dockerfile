# syntax=docker/dockerfile:1
# Container for Upload Receipt Lambda
# Simple presign + DynamoDB job creation endpoint

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Copy only the dependency package (triggers rebuild only when this changes)
COPY receipt_dynamo/ /tmp/receipt_dynamo/

# Install receipt_dynamo (includes boto3 and DynamoDB utilities)
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    rm -rf /tmp/receipt_dynamo

# Stage 2: Copy Lambda-specific code
FROM dependencies

# Bust cache for handler code changes
ARG CACHE_BUST=unknown
RUN echo "Cache bust: $CACHE_BUST"

# Copy the handler module (handler.py is in the container_upload directory)
COPY infra/upload_images/container_upload/handler.py ${LAMBDA_TASK_ROOT}/handler.py

# Set the handler
CMD ["handler.handler"]
