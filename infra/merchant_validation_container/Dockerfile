# syntax=public.ecr.aws/docker/library/dockerfile:1
FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies for ChromaDB and other packages
RUN dnf install -y \
    gcc \
    gcc-c++ \
    python3-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Copy requirements
COPY requirements.txt ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy local packages
COPY ../../../receipt_dynamo ${LAMBDA_TASK_ROOT}/receipt_dynamo/
COPY ../../../receipt_label ${LAMBDA_TASK_ROOT}/receipt_label/

# Copy handler code
COPY handler/handler.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["handler.lambda_handler"]

