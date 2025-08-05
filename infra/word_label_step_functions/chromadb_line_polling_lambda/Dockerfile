# Use AWS Lambda Python base image for ARM64
FROM public.ecr.aws/lambda/python:3.12-arm64

# Install system dependencies for ChromaDB and other packages
RUN dnf install -y \
    gcc-c++ \
    python3-devel \
    && dnf clean all

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Install Python dependencies from PyPI first for better caching
RUN pip install --no-cache-dir \
    chromadb>=0.5.0 \
    numpy>=1.24.0 \
    boto3>=1.34.0 \
    openai>=1.0.0 \
    pydantic>=2.0.0

# Copy local packages
# These paths are relative to the build context (repository root)
COPY receipt_dynamo/ /lambda/receipt_dynamo/
COPY receipt_label/ /lambda/receipt_label/

# Install local packages
RUN cd /lambda && \
    pip install --no-cache-dir ./receipt_dynamo && \
    pip install --no-cache-dir ./receipt_label

# Copy handler code
COPY infra/word_label_step_functions/chromadb_line_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Add /lambda to Python path so imports work
ENV PYTHONPATH=/lambda:$PYTHONPATH

# Command to run the Lambda handler
CMD ["handler.poll_handler"]