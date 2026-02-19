# Multi-stage build for Merge Receipt Lambda
# Merges multiple receipt fragments into a single receipt

# Stage 1: Build and install dependencies (cached layer)
FROM public.ecr.aws/lambda/python:3.12 AS dependencies

# Set environment variable to avoid compilation issues with hnswlib
ENV HNSWLIB_NO_NATIVE=1

# Copy only the dependency packages (triggers rebuild only when these change)
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_dynamo_stream/ /tmp/receipt_dynamo_stream/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_upload/ /tmp/receipt_upload/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/

# Install dependencies from pyproject.toml files in dependency order
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_dynamo_stream /tmp/receipt_chroma /tmp/receipt_upload /tmp/receipt_places /tmp/receipt_agent

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy reusable geometry/records/metadata utilities from combine_receipts
COPY infra/combine_receipts_step_functions/lambdas/geometry_utils.py ${LAMBDA_TASK_ROOT}/geometry_utils.py
COPY infra/combine_receipts_step_functions/lambdas/records_builder.py ${LAMBDA_TASK_ROOT}/records_builder.py
COPY infra/combine_receipts_step_functions/lambdas/metadata_utils.py ${LAMBDA_TASK_ROOT}/metadata_utils.py

# Copy the merge receipt handler
COPY infra/merge_receipt_lambda/lambdas/merge_receipt.py ${LAMBDA_TASK_ROOT}/handler.py

# Set the handler
CMD ["handler.handler"]
