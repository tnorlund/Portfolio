# syntax=docker/dockerfile:1
# ChromaDB Compaction Lambda container
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy the enhanced compaction handler and utils
COPY enhanced_compaction_handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY utils/ ${LAMBDA_TASK_ROOT}/utils/

# Set the handler
CMD ["handler.lambda_handler"]