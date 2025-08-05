# Use AWS Lambda Python base image for ARM64
FROM public.ecr.aws/lambda/python:3.12-arm64

# Install system dependencies for ChromaDB and other packages
RUN dnf install -y \
    gcc-c++ \
    python3-devel \
    && dnf clean all

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy requirements file first (without local package references)
COPY infra/word_label_step_functions/chromadb_polling_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/

# Install Python dependencies from PyPI first
RUN pip install --no-cache-dir \
    chromadb>=0.5.0 \
    numpy>=1.24.0 \
    boto3>=1.34.0 \
    openai>=1.0.0 \
    pydantic>=2.0.0

# Copy the local packages into the container
# These paths are relative to the build context (repository root)
COPY receipt_label/ /lambda/receipt_label/
COPY receipt_dynamo/ /lambda/receipt_dynamo/

# Install local packages
RUN cd /lambda && \
    pip install --no-cache-dir ./receipt_dynamo && \
    pip install --no-cache-dir ./receipt_label

# Copy handler code
COPY infra/word_label_step_functions/chromadb_polling_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Add /lambda to Python path so imports work
ENV PYTHONPATH=/lambda:$PYTHONPATH

# Command to run the Lambda handler
CMD ["handler.poll_handler"]