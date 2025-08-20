# syntax=docker/dockerfile:1
# ChromaDB Compaction Lambda container
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy only the enhanced compaction handler
COPY infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py ${LAMBDA_TASK_ROOT}/handler.py

# Set the handler
CMD ["handler.lambda_handler"]