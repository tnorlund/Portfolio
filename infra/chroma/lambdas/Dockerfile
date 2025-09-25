ARG BASE_IMAGE=chromadb/chroma:latest
FROM ${BASE_IMAGE}

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir boto3

COPY entrypoint.py /entrypoint.py

ENV CHROMA_SERVER_HOST=0.0.0.0 \
    CHROMA_SERVER_HTTP_PORT=8000 \
    CHROMA_PERSIST_DIR=/data/chroma \
    CHROMA_COLLECTION=words

EXPOSE 8000

RUN mkdir -p ${CHROMA_PERSIST_DIR}

ENTRYPOINT ["python", "/entrypoint.py"]


