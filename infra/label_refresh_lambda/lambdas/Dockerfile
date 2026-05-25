FROM public.ecr.aws/lambda/python:3.12

COPY receipt_dynamo/ /tmp/receipt_dynamo/
RUN pip install --no-cache-dir /tmp/receipt_dynamo && rm -rf /tmp/receipt_dynamo

COPY receipt_chroma/ /tmp/receipt_chroma/
RUN pip install --no-cache-dir /tmp/receipt_chroma && rm -rf /tmp/receipt_chroma

COPY infra/label_refresh_lambda/lambdas/label_refresh.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
