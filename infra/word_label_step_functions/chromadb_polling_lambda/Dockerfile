# Use the base image if provided, otherwise use Lambda Python base
ARG BASE_IMAGE
ARG PYTHON_VERSION=3.12
FROM ${BASE_IMAGE:-public.ecr.aws/lambda/python:${PYTHON_VERSION}-arm64}

# If using base image, only install ChromaDB (packages already installed)
# If using vanilla Lambda base, install everything
RUN if [ -z "${BASE_IMAGE}" ]; then \
        dnf install -y gcc-c++ python3-devel && dnf clean all && \
        export HNSWLIB_NO_NATIVE=1; \
    fi

# Install ChromaDB separately since it's not in the base image
RUN pip install --no-cache-dir chromadb>=0.5.0

# Copy and install local packages only if not using base image
RUN if [ -z "${BASE_IMAGE}" ]; then \
        pip install --no-cache-dir \
            numpy>=1.24.0 \
            boto3>=1.34.0 \
            openai>=1.0.0 \
            pydantic>=2.0.0; \
    fi

# Copy the local packages (always needed for handler imports)
COPY receipt_label/ /lambda/receipt_label/
COPY receipt_dynamo/ /lambda/receipt_dynamo/

# Install local packages only if not using base image
RUN if [ -z "${BASE_IMAGE}" ]; then \
        cd /lambda && \
        pip install --no-cache-dir ./receipt_dynamo && \
        pip install --no-cache-dir ./receipt_label; \
    fi

# Copy handler code
COPY infra/word_label_step_functions/chromadb_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Add /lambda to Python path so imports work
ENV PYTHONPATH=/lambda:$PYTHONPATH

# Command to run the Lambda handler
CMD ["handler.poll_handler"]