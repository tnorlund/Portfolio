# syntax=public.ecr.aws/docker/library/dockerfile:1.9.0
# Multi-stage build for ChromaDB ECS Service

# Stage 1: Install ChromaDB and dependencies
# Use AWS ECR mirror to avoid Docker Hub rate limits
FROM public.ecr.aws/docker/library/python:3.12-slim AS dependencies

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install ChromaDB and boto3
RUN pip install --no-cache-dir \
    chromadb \
    boto3

# Stage 2: Final image
FROM dependencies

# Copy the entrypoint script
COPY entrypoint.py /entrypoint.py

# Set environment variables
ENV CHROMA_SERVER_HOST=0.0.0.0 \
    CHROMA_SERVER_HTTP_PORT=8000 \
    CHROMA_PERSIST_DIR=/data/chroma \
    CHROMA_COLLECTION=words

# Create data directory
RUN mkdir -p ${CHROMA_PERSIST_DIR}

# Expose port
EXPOSE 8000

# Set entrypoint
ENTRYPOINT ["python", "/entrypoint.py"]


