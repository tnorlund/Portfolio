FROM public.ecr.aws/lambda/python:3.12

COPY receipt_dynamo/ /tmp/receipt_dynamo/
RUN pip install --no-cache-dir /tmp/receipt_dynamo && rm -rf /tmp/receipt_dynamo

COPY infra/trigger_reocr_lambda/lambdas/trigger_reocr.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.handler"]
