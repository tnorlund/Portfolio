# Removing the paid LangSmith dependency

The application continues using the open-source LangChain, LangGraph, and
LangSmith SDK packages. Deployed workflows no longer require LangSmith bulk
export, a tenant ID, or a LangSmith service API key. Hosted tracing is disabled
by default. This code change does not cancel or change a subscription.

## Replacement paths

| Workflow | Replacement | Preserved behavior |
| --- | --- | --- |
| QA marquee | Run questions → query receipt metadata → build cache in Lambda | Answers, receipt evidence, node/tool timing, cost and token totals |
| Receipt validation visualization | Receipt spans written directly to S3 → one Python Lambda | Receipt images, word decisions, tier timing, existing API payload |
| Retired label evaluator | Keep its existing cache and trace archive | Historical visualization remains available |
| Occasional hosted debugging | Explicit, sampled LangSmith opt-in | Hosted tools available under the chosen account's plan |

Both live cache workflows run without Spark, PySpark, Java, PyArrow, or EMR.
The receipt builder uses the existing DynamoDB Lambda layer and boto3. The EMR
application, job role, and environment builder are retired; historical S3 buckets
remain, with expiration disabled. AWS Lambda, storage, and model charges remain.

## Native records and failure handling

QA stores a completed question at `qa-runs/<execution-id>/qNN.json` immediately.
Retries within the same Step Functions execution reuse that record, including
its cost, rather than repeating the question. A complete batch also has
`question-results.ndjson`. Records include a schema version, trace ID, actual
node/tool inputs and outputs, timing, status/errors, and token/cost totals.

The cache builder writes question files under a unique `cache-runs/` prefix,
then publishes `metadata.json` with a `questions_prefix` pointer. It rejects
incomplete batches and missing receipt metadata. The API supports both this
pointer and the previous `questions/` layout. The manual local QA repair script
uses the currently published prefix.

Receipt processing writes one NDJSON object per completed root under
`native-traces/date=YYYY-MM-DD/<trace-id>-<publisher-run-id>.ndjson` in the existing label-validation
export bucket. Each span includes IDs/parentage, start/end timestamps, status,
inputs, outputs and metadata. JSON payload columns retain the existing export
representation for compatibility with archived records. Lambda worker
threads propagate the native context, and deferred validation carries its trace
identity through the SQS payload. Failed deferred attempts stay in private records
but are excluded from the visualization. Receipt lookups select recent traced
receipts so a new native run does not depend on overlap with a table scan sample.
The Lambda samples the 500 newest native trace objects, includes older objects
for those trace identities (including deferred SQS work), and builds up to 50
receipts. A 100 MiB input limit fails without publishing a partial cache.
Historical Parquet readers remain optional offline tools; no deployed workflow
installs or invokes them.

Receipt caches also use unique prefixes. Their `metadata.json` contains the
complete `receipt_keys` index and is the publication point; failed receipt writes do not publish that index.
The API falls back to the historical layout when the index is absent.

These records replace the application's export dependency, not the entire
hosted trace explorer. They do not reproduce LangSmith experiments, annotation
queues, datasets, feedback UI, or every nested SDK/model span. A hard process
termination can still lose a question or receipt root that has not completed;
already checkpointed QA questions survive. Provider invoices remain the source
of truth for charges incurred during interrupted calls.

## Hosted debugging configuration

The upload, QA, place-fixing, and MCP Lambda environments use one configuration
helper:

| Pulumi configuration | Default | Meaning |
| --- | --- | --- |
| `portfolio:LANGSMITH_TRACING_ENABLED` | `false` | Explicitly enable hosted tracing |
| `portfolio:LANGSMITH_TRACING_SAMPLING_RATE` | `0.1` when enabled | Sample rate from 0 to 1 |
| `portfolio:LANGCHAIN_API_KEY` | Not required when disabled | Secret for the chosen hosted organization |

Both `LANGCHAIN_TRACING_V2` and `LANGSMITH_TRACING` are set explicitly. A retained
API key no longer enables tracing by itself. Native receipt records are governed
by `RECEIPT_TRACE_BUCKET`, which infrastructure supplies with narrowly scoped S3
write permission, and are independent of hosted sampling.

## Rollout and cancellation checklist

1. **Before retiring the existing export resources**, choose the history,
   datasets, annotations and feedback to retain from the paid organization.
   Complete and verify that archive or organization migration while the current
   account still has access. Existing S3 exports are retained by this change,
   but content that exists only in LangSmith has not been archived automatically.
2. Obtain authorization for a dev deployment under the repository's `AGENTS.md`
   rules. Preview the fully qualified dev stack and verify the expected changes:
   retained trace/cache buckets, new native S3 permission and QA builder, retired EMR runtime/build resources and
   export setup/trigger/check Lambdas and their IAM credentials. Bucket resource
   identities stay the same; archive buckets disable `force_destroy` and use
   `retain_on_delete`. Stop on unrelated deletes or replacements.
3. With hosted tracing disabled, run a dev QA batch and inspect its checkpoints,
   NDJSON, cache metadata, and both single-question/all-question API responses.
   Verify costs and errors as well as successful answers. Redrive an interrupted
   batch and verify completed questions are reused.
4. Process a dev receipt, confirm native root/child records in S3, then run the
   label cache workflow. Compare its receipt payload with the prior cache. An
   empty native dataset fails cache generation and leaves the old cache intact;
   it does not manufacture validation history.
5. Review the dev evidence before authorizing a merge and the normal main CI
   deployment. Preserve existing exports and caches during the transition.
6. After live verification and history migration, change/cancel the paid account
   with the owner. If retaining the free Developer plan, use the documented
   organization migration process and configure its tracing limits. Revoke old
   LangSmith credentials and remove unused encrypted configuration only after
   the archive has been verified.

LangSmith documents bulk export as a paid feature and describes migration to a
new free organization rather than an in-place paid-to-free downgrade:
[bulk export](https://docs.langchain.com/langsmith/data-export),
[downgrade guidance](https://kb.langchain.com/articles/9814546813-can-i-downgrade-my-organization-from-a-paid-plan-to-a-free-plan),
[pricing and usage](https://www.langchain.com/pricing).

No subscription change, live deployment, or live history export is performed by
the offline tests. Fixed subscription savings depend on the actual seat count;
net savings also depend on AWS usage and any optional hosted trace usage.
