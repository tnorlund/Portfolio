# Receipt Dynamo

A Python package for accessing DynamoDB for receipt data. This package provides a robust interface for interacting with receipt data stored in AWS DynamoDB.

## Package Responsibilities

**IMPORTANT**: This package is the ONLY place where DynamoDB-specific logic should exist in the codebase.

### What belongs in receipt_dynamo:

- ✅ ALL DynamoDB client implementations
- ✅ ALL DynamoDB operations (queries, writes, batch operations)
- ✅ ALL DynamoDB resilience patterns (circuit breakers, retries, batching)
- ✅ Entity definitions and DynamoDB item conversions
- ✅ DynamoDB-specific error handling and recovery

### What does NOT belong here:

- ❌ Business logic (belongs in receipt_label)
- ❌ AI service integrations (belongs in receipt_label)
- ❌ OCR processing (belongs in receipt_ocr)
- ❌ Any imports from receipt_label or receipt_ocr

### Example: Resilient DynamoDB Operations

All resilience patterns for DynamoDB are implemented in this package:

```python
from receipt_dynamo import ResilientDynamoClient

# This client includes circuit breaker, retry logic, and batching
client = ResilientDynamoClient(
    table_name="my-table",
    circuit_breaker_threshold=5,
    max_retry_attempts=3,
    batch_size=25
)
```

Other packages should use these high-level interfaces instead of implementing their own DynamoDB logic.

## Package Structure

The package has been moved from `infra/lambda_layer/python/dynamo` to the top-level `receipt_dynamo` directory and renamed from `dynamo` to `receipt_dynamo`.

## Installation

### Development Installation

For development, you can install the package in editable mode:

```bash
cd receipt_dynamo
pip install -e .
```

### Installing Dependencies

```bash
# Install package with test dependencies
pip install -e ".[test]"

# For development (includes type stubs for better IDE support)
pip install -e ".[dev]"
```

### Type Safety

This package uses boto3 type stubs for improved developer experience. Type annotations are implemented using the TYPE_CHECKING pattern to avoid runtime dependencies. See [CODEX.md](../CODEX.md) for implementation details.

## Testing

Run tests with pytest:

```bash
cd receipt_dynamo
pytest
```

## AWS Lambda Layer

This package is also deployed as an AWS Lambda Layer using Pulumi. The deployment is managed in `infra/lambda_layer.py`.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Table Design

| Item Type                       | PK                       | SK                                                                                     | GSI1 PK                       | GSI1 SK                                                               | GSI2 PK                             | GSI2 SK                                                               | GSI3 PK                                 | GSI3 SK                                                            | Attributes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| :------------------------------ | :----------------------- | :------------------------------------------------------------------------------------- | :---------------------------- | :-------------------------------------------------------------------- | :---------------------------------- | :-------------------------------------------------------------------- | :-------------------------------------- | :----------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Image**                       | `IMAGE#<image_id>`       | `IMAGE`                                                                                | `IMAGE#<image_id>`            | `IMAGE`                                                               |                                     |                                                                       |                                         |                                                                    | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `sha256`                                                                                                                                                                                                                                                                                                                                                                                                      |
| **Line**                        | `IMAGE#<image_id>`       | `LINE#<line_id>`                                                                       | `IMAGE#<image_id>`            | `LINE#<line_id>`                                                      |                                     |                                                                       |                                         |                                                                    | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Word**                        | `IMAGE#<image_id>`       | `LINE#<line_id>#WORD#<word_id>`                                                        |                               |                                                                       |                                     |                                                                       |                                         |                                                                    | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Letter**                      | `IMAGE#<image_id>`       | `LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>`                                     |                               |                                                                       |                                     |                                                                       |                                         |                                                                    | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Receipt**                     | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>`                                                                 | `IMAGE#<image_id>`            | `RECEIPT#<receipt_id>`                                                | `RECEIPT`                           | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                               |                                         |                                                                    | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `sha256`                                                                                                                                                                                                                                                                                                                                            |
| **ReceiptMetadata**             | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#METADATA`                                                        | `MERCHANT#<merchant_name>`    | `IMAGE#<image_id>#RECEIPT#<receipt_id>#METADATA`                      | `MERCHANT_VALIDATION`               | `CONFIDENCE#<score>`                                                  |                                         |                                                                    | - `place_id` <br> - `merchant_name` <br> - `merchant_category` <br> - `address` <br> - `phone_number` <br> - `match_confidence` <br> - `matched_fields` <br> - `validated_by` <br> - `timestamp` <br> - `reasoning`                                                                                                                                                                                                                                                                                   |
| **Receipt Line**                | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>`                                                  | `EMBEDDING_STATUS#<status>`   | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>`                |                                     |                                                                       | `IMAGE#<image_id>#RECEIPT#<receipt_id>` | `LINE`                                                             | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Receipt Word**                | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`                                   | `EMBEDDING_STATUS#<status>`   | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>` | `RECEIPT`                           | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>` | `IMAGE#<image_id>#RECEIPT#<receipt_id>` | `WORD`                                                             | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Receipt Word Label**          | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>`                     | `LABEL#<label>`               | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>` | `RECEIPT`                           | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>` | `VALIDATION_STATUS#<status>`            | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>` | - `reasoning` <br>- `timestamp_added` <br>- `validation_status` <br>- `label_consolidated_from` <br>- `label_proposed_by`                                                                                                                                                                                                                                                                                                                                                                             |
| **Receipt Field**               | `FIELD#<field_type>`     | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                                | `IMAGE#<image_id>`            | `RECEIPT#<receipt_id>#FIELD#<field_type>`                             |                                     |                                                                       |                                         |                                                                    | - `words: [{word_id, line_id, label}]` <br>- `reasoning` <br>- `timestamp_added`                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **Receipt Letter**              | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>`                |                               |                                                                       |                                     |                                                                       |                                         |                                                                    | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`                                                                                                                                                                                                                                                                                                                                                   |
| **Receipt Label Analysis**      | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#LABELS`                                                 | `ANALYSIS_TYPE`               | `LABELS#<timestamp>`                                                  |                                     |                                                                       |                                         |                                                                    | - `labels: [{label_type, line_id, word_id, text, reasoning, bounding_box: {top_left, top_right, bottom_left, bottom_right}}]` <br>- `timestamp_added` <br>- `version` <br>- `overall_reasoning` <br>- `metadata: {processing_metrics: {processing_time_ms, api_calls}, processing_history: [{event_type, timestamp, description, model_version}], source_information: {model_name, model_version, algorithm, configuration}}`                                                                         |
| **Receipt Structure Analysis**  | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#STRUCTURE`                                              | `ANALYSIS_TYPE`               | `STRUCTURE#<timestamp>`                                               |                                     |                                                                       |                                         |                                                                    | - `sections: [{name, line_ids, reasoning, spatial_patterns: [{pattern_type, description, metadata}], content_patterns: [{pattern_type, description, metadata}]}]` <br>- `overall_reasoning` <br>- `timestamp_added` <br>- `version` <br>- `metadata: {processing_metrics: {processing_time_ms, api_calls}, processing_history: [{event_type, timestamp, description, model_version}], source_information: {model_name, model_version, algorithm, configuration}}`                                     |
| **Receipt Line Item Analysis**  | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#LINE_ITEMS`                                             | `ANALYSIS_TYPE`               | `LINE_ITEMS#<timestamp>`                                              |                                     |                                                                       |                                         |                                                                    | - `items: [{description, quantity: {amount, unit}, price: {unit_price, extended_price}, line_ids, reasoning}]` <br>- `subtotal` <br>- `tax` <br>- `total` <br>- `total_found: bool` <br>- `discrepancies: []` <br>- `reasoning` <br>- `timestamp_added` <br>- `version` <br>- `metadata: {processing_metrics: {processing_time_ms, api_calls}, processing_history: [{event_type, timestamp, description, model_version}], source_information: {model_name, model_version, algorithm, configuration}}` |
| **Receipt Validation Summary**  | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#VALIDATION`                                             | `ANALYSIS_TYPE`               | `VALIDATION#<timestamp>`                                              |                                     |                                                                       | `VALIDATION_STATUS#<status>`            | `TIMESTAMP#<timestamp>`                                            | - `overall_status` <br>- `overall_reasoning` <br>- `validation_timestamp` <br>- `version` <br>- `field_summary: {field_name: {status, count, has_errors, has_warnings}}` <br>- `metadata: {processing_metrics: {processing_time_ms, api_calls}, processing_history: [{event_type, timestamp, description, model_version}], source_information: {model_name, model_version, algorithm, configuration}}`                                                                                                |
| **Receipt Validation Category** | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#VALIDATION#CATEGORY#<field_name>`                       | `ANALYSIS_TYPE`               | `VALIDATION#<timestamp>#CATEGORY#<field_name>`                        |                                     |                                                                       | `FIELD_STATUS#<field_name>#<status>`    | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                            | - `field_category` <br>- `status` <br>- `reasoning` <br>- `result_summary: {error_count, warning_count, info_count, success_count}` <br>- `metadata`                                                                                                                                                                                                                                                                                                                                                  |
| **Receipt Validation Result**   | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#VALIDATION#CATEGORY#<field_name>#RESULT#<result_index>` | `ANALYSIS_TYPE`               | `VALIDATION#<timestamp>#CATEGORY#<field_name>#RESULT`                 |                                     |                                                                       | `RESULT_TYPE#<type>`                    | `IMAGE#<image_id>#RECEIPT#<receipt_id>#CATEGORY#<field_name>`      | - `type: "error\|warning\|info\|success"` <br>- `message` <br>- `reasoning` <br>- `field` <br>- `expected_value` <br>- `actual_value` <br>- `metadata`                                                                                                                                                                                                                                                                                                                                                |
| **Receipt ChatGPT Validation**  | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#VALIDATION#CHATGPT#<timestamp>`                         | `ANALYSIS_TYPE`               | `VALIDATION_CHATGPT#<timestamp>`                                      |                                     |                                                                       | `VALIDATION_STATUS#<status>`            | `CHATGPT#<timestamp>`                                              | - `original_status` <br>- `revised_status` <br>- `reasoning` <br>- `corrections: [{field_name, original_value, corrected_value, explanation}]` <br>- `metadata` <br>- `prompt` <br>- `response`                                                                                                                                                                                                                                                                                                       |
| **Compaction Run**              | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#COMPACTION_RUN#<run_id>`                                         | `RUNS`                        | `CREATED_AT#<timestamp>`                                              |                                     |                                                                       |                                         |                                                                    | - `run_id` <br>- `image_id` <br>- `receipt_id` <br>- `lines_delta_prefix` <br>- `words_delta_prefix` <br>- `lines_state` <br>- `words_state` <br>- `lines_started_at` <br>- `lines_finished_at` <br>- `words_started_at` <br>- `words_finished_at` <br>- `lines_error` <br>- `words_error` <br>- `lines_merged_vectors` <br>- `words_merged_vectors` <br>- `created_at` <br>- `updated_at`                                                                                                            |
| **Places Cache**                | `PLACES#<search_type>`   | `VALUE#<search_value>`                                                                 | `PLACE_ID`                    | `PLACE_ID#<place_id>`                                                 | `LAST_USED`                         | `<timestamp>`                                                         |                                         |                                                                    | - `place_id` <br>- `places_response` <br>- `last_updated` <br>- `query_count` <br>- `search_type` <br>- `search_value` <br>- `validation_status` <br>- `normalized_value` <br>- `value_hash`                                                                                                                                                                                                                                                                                                          |
| **Job**                         | `JOB#<job_id>`           | `JOB`                                                                                  | `STATUS#<status>`             | `CREATED#<timestamp>`                                                 | `USER#<user>`                       | `CREATED#<timestamp>`                                                 |                                         |                                                                    | - `name` <br>- `description` <br>- `created_at` <br>- `created_by` <br>- `status` <br>- `priority` <br>- `job_config` <br>- `estimated_duration` <br>- `tags`                                                                                                                                                                                                                                                                                                                                         |
| **Job Status**                  | `JOB#<job_id>`           | `STATUS#<timestamp>`                                                                   | `STATUS#<status>`             | `UPDATED#<timestamp>`                                                 |                                     |                                                                       |                                         |                                                                    | - `status` <br>- `progress` <br>- `message` <br>- `updated_at` <br>- `updated_by` <br>- `instance_id`                                                                                                                                                                                                                                                                                                                                                                                                 |
| **Job Resource**                | `JOB#<job_id>`           | `RESOURCE#<resource_id>`                                                               | `RESOURCE`                    | `RESOURCE#<resource_id>`                                              |                                     |                                                                       |                                         |                                                                    | - `resource_type` <br>- `instance_id` <br>- `instance_type` <br>- `gpu_count` <br>- `allocated_at` <br>- `released_at` <br>- `status`                                                                                                                                                                                                                                                                                                                                                                 |
| **Job Metric**                  | `JOB#<job_id>`           | `METRIC#<metric_name>#<timestamp>`                                                     | `METRIC#<metric_name>`        | `<timestamp>`                                                         | `METRIC#<metric_name>`              | `JOB#<job_id>#<timestamp>`                                            |                                         |                                                                    | - `metric_name` <br>- `value` <br>- `unit` <br>- `timestamp` <br>- `step` <br>- `epoch`                                                                                                                                                                                                                                                                                                                                                                                                               |
| **Job Checkpoint**              | `JOB#<job_id>`           | `CHECKPOINT#<timestamp>`                                                               | `CHECKPOINT`                  | `JOB#<job_id>#<timestamp>`                                            |                                     |                                                                       |                                         |                                                                    | - `s3_bucket` <br>- `s3_key` <br>- `size_bytes` <br>- `model_state` <br>- `optimizer_state` <br>- `metrics` <br>- `step` <br>- `epoch` <br>- `is_best`                                                                                                                                                                                                                                                                                                                                                |
| **Job Log**                     | `JOB#<job_id>`           | `LOG#<timestamp>`                                                                      | `LOG`                         | `JOB#<job_id>#<timestamp>`                                            |                                     |                                                                       |                                         |                                                                    | - `log_level` <br>- `message` <br>- `source` <br>- `exception`                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **Job Dependency**              | `JOB#<job_id>`           | `DEPENDS_ON#<dependency_job_id>`                                                       | `DEPENDENCY`                  | `DEPENDENT#<job_id>#DEPENDENCY#<dependency_job_id>`                   | `DEPENDENCY`                        | `DEPENDED_BY#<dependency_job_id>#DEPENDENT#<job_id>`                  |                                         |                                                                    | - `type` <br>- `condition` <br>- `created_at`                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Instance**                    | `INSTANCE#<instance_id>` | `INSTANCE`                                                                             | `STATUS#<status>`             | `INSTANCE#<instance_id>`                                              |                                     |                                                                       |                                         |                                                                    | - `instance_type` <br>- `gpu_count` <br>- `status` <br>- `launched_at` <br>- `ip_address` <br>- `availability_zone` <br>- `is_spot` <br>- `health_status`                                                                                                                                                                                                                                                                                                                                             |
| **Instance Job**                | `INSTANCE#<instance_id>` | `JOB#<job_id>`                                                                         | `JOB`                         | `JOB#<job_id>#INSTANCE#<instance_id>`                                 |                                     |                                                                       |                                         |                                                                    | - `assigned_at` <br> - `status` <br> - `resource_utilization`                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Queue**                       | `QUEUE#<queue_name>`     | `QUEUE`                                                                                | `QUEUE`                       | `QUEUE#<queue_name>`                                                  |                                     |                                                                       |                                         |                                                                    | - `description` <br>- `created_at` <br>- `max_concurrent_jobs` <br>- `priority` <br>- `job_count`                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Queue Job**                   | `QUEUE#<queue_name>`     | `JOB#<job_id>`                                                                         | `JOB`                         | `JOB#<job_id>#QUEUE#<queue_name>`                                     |                                     |                                                                       |                                         |                                                                    | - `enqueued_at` <br>- `priority` <br>- `position`                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Completion Batch Result**     | `BATCH#<batch_id>`       | `RESULT#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<original_label>`     | `LABEL_TARGET#<label_target>` | `STATUS#<status>`                                                     | `BATCH#<batch_id>`                  | `STATUS#<status>`                                                     | `IMAGE#<image_id>#RECEIPT#<receipt_id>` | `BATCH#<batch_id>#STATUS#<status>`                                 | - `original_label` <br> - `gpt_suggested_label` <br> - `label_confidence` <br> - `label_changed` <br> - `status` <br> - `validated_at` <br> - `reasoning` <br> - `raw_prompt` <br> - `raw_response` <br> - `label_target`                                                                                                                                                                                                                                                                             |
| **Embedding Batch Result**      | `BATCH#<batch_id>`       | `RESULT#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>`              |                               |                                                                       | `BATCH#<batch_id>`                  | `STATUS#<status>`                                                     | `IMAGE#<image_id>#RECEIPT#<receipt_id>` | `BATCH#<batch_id>#STATUS#<status>`                                 | - `pinecone_id` <br> - `text` <br> - `label` <br> - `status` <br> - `error_message`                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Batch Summary**               | `BATCH#<batch_id>`       | `STATUS`                                                                               | `STATUS#<status>`             | `BATCH_TYPE#<batch_type>#BATCH_ID#<batch_id>`                         |                                     |                                                                       |                                         |                                                                    | - `batch_type` <br> - `openai_batch_id` <br> - `submitted_at` <br> - `status` <br> - `word_count` <br> - `result_file_id` <br> - `receipt_refs`                                                                                                                                                                                                                                                                                                                                                       |
| **Label Hygiene Result**        | `LABEL_HYGIENE#<id>`     | `FROM#<alias>#TO#<canonical_label>`                                                    | `ALIAS#<alias>`               | `TO#<canonical_label>`                                                | `CANONICAL_LABEL#<canonical_label>` | `ALIAS#<alias>`                                                       |                                         |                                                                    | - `reasoning` <br> - `gpt_agreed` <br> - `source_batch_id` <br> - `example_ids` <br> - `image_id` <br> - `receipt_id` <br> - `timestamp`                                                                                                                                                                                                                                                                                                                                                              |
| **Label Metadata**              | `LABEL#<label>`          | `METADATA`                                                                             | `LABEL#<label>`               | `METADATA`                                                            | `LABEL_TARGET#<target>`             | `LABEL#<label>`                                                       |                                         |                                                                    | - `status` <br> - `aliases` <br> - `description` <br> - `schema_version` <br> - `last_updated` <br> - `label_target` <br> - `receipt_refs`                                                                                                                                                                                                                                                                                                                                                            |

## Key Design Notes

1. **Unified Partition Key**:

   - All items related to an image (lines, words, word tags, receipts) share the same partition key (`PK = IMAGE#<image_id>`).

2. **Metadata Structure**:

   - All analysis items include a standardized metadata structure:
     - `processing_metrics`: Runtime performance measurements like processing time and API call counts
     - `processing_history`: Chronological log of creation and modification events
     - `source_information`: Details about the source model, algorithm, and configuration

3. **Position Representation**:

   - Positions are represented using a standardized bounding box structure with top-left, top-right, bottom-left, and bottom-right points
   - This structure is used consistently across all relevant items

4. **Reasoning-Based Approach**:

   - The system uses detailed reasoning instead of confidence scores throughout
   - Each analysis includes both specific reasoning for individual elements and overall reasoning

5. **Validation Structure**:

   - The validation system uses a standardized format with type, message, and reasoning fields
   - Validation types include: error, warning, info, and success
   - Validation status values are standardized across the system

6. **Split Storage for Validation Analysis**:

   - Validation analysis is split across multiple items to handle large volumes of validation data
   - Main summary item contains the overall status and a summary of all field validations
   - Each validation category has its own item with category-specific status and reasoning
   - Individual validation results are stored as separate items linked to their parent category
   - ChatGPT second-pass validations are stored as separate items that reference the original validation

## Hyperparameter Sweeps

The DynamoDB table design supports efficient hyperparameter sweeps for model training. This section explains how the various entities work together to implement robust hyperparameter exploration.

### Entity Relationships for Hyperparameter Sweeps

1. **Job Hierarchy**:

   - A parent **Job** entity represents the entire sweep with `job_type = 'sweep'`
   - Multiple child **Job** entities represent individual training runs with `job_type = 'training'`
   - The sweep configuration including parameter grid is stored in the parent job's `job_config`

2. **Job Dependencies**:

   - **Job Dependency** entities connect child jobs to the parent sweep job
   - This creates a directed graph where:
     - Each child job depends on the parent sweep job (for initialization/configuration)
     - The parent sweep job depends on all child jobs (for completion status)
   - The dependency type is stored as `type = 'parent_sweep'` or `type = 'child_training'`
   - Dependencies enable tracking of the entire sweep's progress

3. **Queue Management**:

   - **Queue Job** entities place each child job in processing queues
   - All jobs from a sweep typically use the same queue, but with different priorities
   - The `sweep_id` attribute in **Queue Job** enables filtering jobs by sweep
   - Jobs can be prioritized based on early results (e.g., for Bayesian optimization)

4. **Results Tracking**:

   - **Job Metric** entities store the metrics for each hyperparameter combination
   - Metrics are linked to both the child job and parent sweep job
   - The metric structure enables querying for the best-performing combinations
   - Standardized metric names (e.g., `val_loss`, `val_accuracy`) facilitate comparison

5. **Resilience Mechanisms**:
   - **Job Checkpoint** entities ensure training can resume if interrupted
   - **Job Status** entities track detailed progress of each training job
   - This combination provides fault tolerance for long-running sweeps

### Data Flow During a Hyperparameter Sweep

1. **Sweep Initialization**:

   ```
   ┌────────────────┐      ┌────────────────┐
   │ Parent Sweep   │──────► Job Dependencies│
   │ Job            │      │ (Parent→Child)  │
   └────────────────┘      └────────────────┘
          │                        │
          ▼                        ▼
   ┌────────────────┐      ┌────────────────┐
   │ Child Training │◄─────┤ Queue Jobs     │
   │ Jobs           │      │                │
   └────────────────┘      └────────────────┘
   ```

2. **Job Processing**:

   ```
   ┌────────────────┐      ┌────────────────┐
   │ Child Training │──────► Job Status     │
   │ Job            │      │ Updates        │
   └────────────────┘      └────────────────┘
          │                        │
          ▼                        ▼
   ┌────────────────┐      ┌────────────────┐
   │ Job Checkpoints│      │ Job Metrics    │
   │                │      │                │
   └────────────────┘      └────────────────┘
   ```

3. **Results Aggregation**:
   ```
   ┌────────────────┐      ┌────────────────┐
   │ Job Metrics    │──────► Parent Sweep   │
   │ (All Jobs)     │      │ Job (Updates)  │
   └────────────────┘      └────────────────┘
          │                        │
          ▼                        ▼
   ┌────────────────┐      ┌────────────────┐
   │ Best Parameters│      │ Completion     │
   │ Identification │      │ Status         │
   └────────────────┘      └────────────────┘
   ```

### Example Query Patterns

1. **Find all jobs in a sweep**:

   - Query using GSI2 with `GSI2PK = "DEPENDENCY"` and `GSI2SK` beginning with `"DEPENDED_BY#<sweep_id>"`

2. **Find all metrics for a sweep**:

   - Query using PK = `"JOB#<sweep_id>"` and SK beginning with `"METRIC#"`

3. **Find best-performing configuration**:

   - Query all metrics for a sweep
   - Sort by the target metric value
   - Select the hyperparameters from the top result

4. **Resume incomplete sweep**:

   - Find all child jobs with status != "COMPLETED"
   - Re-queue these jobs with their latest checkpoints

5. **Early stopping of poorly performing jobs**:
   - Compare metrics after N epochs across all running jobs
   - Update low-performing jobs to status "STOPPED"

### Implementation Considerations

1. **Atomicity**: Use transactions when creating or updating multiple related entities
2. **Pagination**: For large sweeps, implement pagination when querying results
3. **TTL**: Consider setting TTL for intermediate metrics to manage storage costs
4. **GSI Design**: The GSIs are optimized for common query patterns in sweep workflows
5. **Concurrency**: The design supports multiple sweeps running concurrently

The hyperparameter sweep functionality demonstrates how the entity design provides a flexible foundation for complex ML workflows while maintaining data integrity and queryability.
