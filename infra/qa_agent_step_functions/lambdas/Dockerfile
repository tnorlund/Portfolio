FROM public.ecr.aws/lambda/python:3.12 AS dependencies
ENV HNSWLIB_NO_NATIVE=1

COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_dynamo_stream/ /tmp/receipt_dynamo_stream/
COPY receipt_chroma/ /tmp/receipt_chroma/
COPY receipt_places/ /tmp/receipt_places/
COPY receipt_agent/ /tmp/receipt_agent/

RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_dynamo_stream && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    pip install --no-cache-dir /tmp/receipt_places && \
    pip install --no-cache-dir /tmp/receipt_agent && \
    rm -rf /tmp/*

FROM dependencies
COPY infra/qa_agent_step_functions/lambdas/run_question.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.handler"]
