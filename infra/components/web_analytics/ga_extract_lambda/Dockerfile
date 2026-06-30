# GA4 extractor Lambda (container image — google-analytics-data is too heavy to
# vendor inline, ~52MB; a container keeps it simple and matches the repo's other
# container Lambdas). boto3 ships in the AWS base image.
FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir "google-analytics-data>=0.18,<1"

COPY infra/components/web_analytics/ga_extract_lambda/handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
