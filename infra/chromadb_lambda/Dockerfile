# Use AWS Lambda Python base image for ARM64
FROM public.ecr.aws/lambda/python:3.12-arm64

# Install system dependencies for ChromaDB compilation
RUN dnf install -y \
    gcc-c++ \
    python3-devel \
    && dnf clean all

# Set environment variable to avoid compilation issues
ENV HNSWLIB_NO_NATIVE=1

# Copy requirements file
COPY requirements.txt ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy handler code
COPY handler.py ${LAMBDA_TASK_ROOT}/
COPY s3_chromadb_handler.py ${LAMBDA_TASK_ROOT}/

# Command to run the Lambda handler
CMD ["s3_chromadb_handler.lambda_handler"]