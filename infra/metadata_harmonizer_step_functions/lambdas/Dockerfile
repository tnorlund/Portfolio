# Multi-stage build for Metadata Harmonizer Lambda

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
# receipt_dynamo_stream depends on receipt_dynamo
# receipt_chroma depends on receipt_dynamo_stream
# receipt_agent depends on receipt_dynamo, receipt_chroma, receipt_upload, and receipt_places
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_upload && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_dynamo_stream /tmp/receipt_chroma /tmp/receipt_upload /tmp/receipt_places /tmp/receipt_agent

# Stage 2: Copy Lambda-specific code (changes frequently, but doesn't invalidate deps cache)
FROM dependencies

# Copy the harmonize metadata handler
COPY infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py ${LAMBDA_TASK_ROOT}/handler.py

# Copy utils module (EMF metrics, etc.)
COPY infra/metadata_harmonizer_step_functions/lambdas/utils/ ${LAMBDA_TASK_ROOT}/utils/

# Set the handler
CMD ["handler.handler"]




