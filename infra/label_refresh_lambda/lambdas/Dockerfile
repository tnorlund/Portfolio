FROM public.ecr.aws/lambda/python:3.13

# Local package install chain. receipt_chroma depends on receipt_embeddings,
# which depends on receipt_dynamo, so install all three locally.
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_embeddings/ /tmp/receipt_embeddings/
COPY receipt_chroma/ /tmp/receipt_chroma/
RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_embeddings && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_embeddings /tmp/receipt_chroma

COPY infra/label_refresh_lambda/lambdas/label_refresh.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
