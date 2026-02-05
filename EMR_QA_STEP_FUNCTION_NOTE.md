# EMR QA Step Function EntryPoint Path Mismatch

## Summary
There appears to be a mismatch between the S3 path where Spark entrypoint scripts are **uploaded** and the path the QA Step Function **invokes**.

- **Uploader (infra/components/emr_serverless_analytics/__init__.py)** uploads:
  - `s3://<spark_artifacts_bucket>/spark/merged_job.py`
  - `s3://<spark_artifacts_bucket>/spark/label_validation_viz_cache_job.py`
- **QA Step Function (infra/qa_agent_step_functions/infrastructure.py)** references:
  - `s3://<spark_artifacts_bucket>/scripts/merged_job.py`

If `scripts/merged_job.py` does not exist in the artifacts bucket, EMR Serverless will fail to start the job.

## What this affects
- **This is not the Docker image.** The image only installs the `receipt_langsmith` package.
- **This is the Spark job entrypoint.** The `EntryPoint` is pulled from S3 at runtime.

## How to verify
Run these commands (from a machine with AWS access):

```bash
aws s3api head-object --bucket <spark-artifacts-bucket> --key spark/merged_job.py
aws s3api head-object --bucket <spark-artifacts-bucket> --key scripts/merged_job.py
```

Check previous successful QA runs:

```bash
aws stepfunctions list-executions \
  --state-machine-arn <qa-step-function-arn> \
  --status-filter SUCCEEDED \
  --max-items 5

aws stepfunctions get-execution-history \
  --execution-arn <execution-arn> \
  --max-items 200
```

Check EMR job history:

```bash
aws emr-serverless list-job-runs \
  --application-id <emr-app-id> \
  --max-results 10
```

## Fix options
1. **Preferred:** update QA Step Function to use the uploaded path:
   - `s3://<spark_artifacts_bucket>/spark/merged_job.py`
2. **Alternative:** update the uploader to also copy `merged_job.py` to `scripts/` for backward compatibility.
3. **Both** (safe transition) if you want to avoid breaking older references.

## Current limitation
This environment cannot reach `api.pulumi.com` or `s3.us-east-1.amazonaws.com`, so AWS CLI validation could not be performed here.
