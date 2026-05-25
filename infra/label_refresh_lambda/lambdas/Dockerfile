FROM public.ecr.aws/lambda/python:3.12

# Local package install chain. receipt_chroma depends on receipt_dynamo_stream
# which depends on receipt_dynamo. All three must be COPYed into the build
# context and installed in order (pip won't find them on PyPI). Matches the
# pattern used by every other chroma-consuming Dockerfile in this repo
# (combine_receipts, fix_place, chromadb_compaction).
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_dynamo_stream/ /tmp/receipt_dynamo_stream/
COPY receipt_chroma/ /tmp/receipt_chroma/
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_dynamo_stream /tmp/receipt_chroma

COPY infra/label_refresh_lambda/lambdas/label_refresh.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
