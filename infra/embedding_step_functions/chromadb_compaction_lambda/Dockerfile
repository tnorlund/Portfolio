# Use AWS Lambda Python base image for ARM64
FROM public.ecr.aws/lambda/python:3.12-arm64

# Install system dependencies for ChromaDB compilation
RUN dnf install -y \
    gcc-c++ \
    python3-devel \
    && dnf clean all

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy requirements file and handler code
# These paths are relative to the build context (repository root)
COPY infra/word_label_step_functions/chromadb_compaction_lambda/requirements.txt ${LAMBDA_TASK_ROOT}/
COPY infra/word_label_step_functions/chromadb_compaction_lambda/handler.py ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Command to run the Lambda handler
CMD ["handler.compact_handler"]