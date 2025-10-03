# syntax=docker/dockerfile:1
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Mirror working pattern from other container builds; copy only worker sources
COPY infra/chromadb_compaction/worker/ ${LAMBDA_TASK_ROOT}/

# Use Lambda-style handler for container-based Lambda
CMD ["worker.lambda_handler"]
