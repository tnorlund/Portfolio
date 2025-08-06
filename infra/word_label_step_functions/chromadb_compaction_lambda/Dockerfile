# Use the base image if provided, otherwise use Lambda Python base
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}-arm64}

# Install system dependencies only if not using base image
RUN if [ -z "${BASE_IMAGE}" ]; then \
        dnf install -y gcc-c++ python3-devel && dnf clean all && \
        export HNSWLIB_NO_NATIVE=1; \
    fi

# Install ChromaDB separately since it's not in the base image
RUN pip install --no-cache-dir chromadb>=0.5.0

# Copy requirements file and handler code
COPY infra/word_label_step_functions/chromadb_compaction_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/
COPY infra/word_label_step_functions/chromadb_compaction_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Install additional dependencies only if not using base image
RUN if [ -z "${BASE_IMAGE}" ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# Command to run the Lambda handler
CMD ["handler.compact_handler"]