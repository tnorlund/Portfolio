# Receipt Dynamo

A Python package for accessing DynamoDB for receipt data. This package provides a robust interface for interacting with receipt data stored in AWS DynamoDB.

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
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For development dependencies
```

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

| Item Type                      | PK                       | SK                                                                      | GSI1 PK                 | GSI1 SK                                                                         | GSI2 PK                | GSI2 SK                                                                         | GSI3 PK            | GSI3 SK                                               | Attributes                                                                                                             |
|:-------------------------------|:-------------------------|:------------------------------------------------------------------------|:------------------------|:--------------------------------------------------------------------------------|:-----------------------|:--------------------------------------------------------------------------------|:-------------------|:------------------------------------------------------|:----------------------------------------------------------------------------------------------------------|
| **Image**                      | `IMAGE#<image_id>`       | `IMAGE`                                                                 | `IMAGE`                 | `IMAGE#<image_id>`                                                              |                        |                                                                                 |                    |                                                       | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `sha256`                      |
| **Line**                       | `IMAGE#<image_id>`       | `LINE#<line_id>`                                                        | `IMAGE`                 | `IMAGE#<image_id>#LINE#<line_id>`                                               |                        |                                                                                 |                    |                                                       | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Word**                       | `IMAGE#<image_id>`       | `LINE#<line_id>#WORD#<word_id>`                                         |                         |                                                                                 |                        |                                                                                 |                    |                                                       | - `text` <br>- `tags` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Word Label**                 | `IMAGE#<image_id>`       | `LINE#<line_id>#WORD#<word_id>#LABEL#<label>`                           | `LABEL#<label>`         | `IMAGE#<image_id>#LINE#<line_id>#WORD#<word_id>`                                |                        |                                                                                 |                    |                                                       | - `confidence` <br>- `timestamp_added`                                                                                  |
| **Group**                      | `GROUP#<group_type>`     | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                 | `IMAGE#<image_id>`      | `RECEIPT#<receipt_id>#GROUP#<group_type>`                                       |                        |                                                                                 |                    |                                                       | - `words: [{word_id, line_id, label}]` <br>- `confidence` <br>- `timestamp_added`                                      |
| **Letter**                     | `IMAGE#<image_id>`       | `LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>`                      |                         |                                                                                 |                        |                                                                                 |                    |                                                       | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Receipt**                    | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>`                                                  | `IMAGE`                 | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         | `IMAGE#<image_id>` | `RECEIPT#<receipt_id>`                                | - `width` <br>- `height` <br>- `timestamp_added` <br>- `s3_bucket` <br>- `s3_key` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `sha256`       |
| **Receipt Line**               | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>`                                   |                         |                                                                                 |                        |                                                                                 |                    |                                                       | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence`         |
| **Receipt Word**               | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`                    |                         |                                                                                 | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`           | `IMAGE#<image_id>` | `#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>` | - `text` <br>- `tags` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Receipt Word Label**         | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LABEL#<label>`      | `LABEL#<label>`         | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`           | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>`           |                    |                                                       | - `confidence` <br>- `timestamp_added`                                                                                  |
| **Receipt Group**              | `GROUP#<group_type>`     | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                 | `IMAGE#<image_id>`      | `RECEIPT#<receipt_id>#GROUP#<group_type>`                                       | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         |                    |                                                       | - `words: [{word_id, line_id, label}]` <br>- `confidence` <br>- `timestamp_added`                                      |
| **Receipt Letter**             | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>#LETTER#<letter_id>` |                         |                                                                                 |                        |                                                                                 |                    |                                                       | - `text` <br>- `top_right` <br>- `top_left` <br>- `bottom_right` <br>- `bottom_left` <br>- `angle_degrees` <br>- `angle_radians` <br>- `confidence` |
| **Receipt Label Analysis**     | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#LABELS`                                  | `ANALYSIS_TYPE`         | `LABELS#<timestamp>`                                                            | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         |                    |                                                       | - `labels: [{label_type, line_id, word_id, text, confidence, position}]` <br>- `timestamp_added` <br>- `version` <br>- `overall_confidence` |
| **Receipt Structure Analysis** | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#STRUCTURE`                               | `ANALYSIS_TYPE`         | `STRUCTURE#<timestamp>`                                                         | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         |                    |                                                       | - `sections: [{name, line_ids, confidence, spatial_patterns, content_patterns}]` <br>- `overall_confidence` <br>- `timestamp_added` <br>- `version` |
| **Receipt Line Items**         | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#LINE_ITEMS`                              | `ANALYSIS_TYPE`         | `LINE_ITEMS#<timestamp>`                                                        | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         |                    |                                                       | - `items: [{description, quantity, price, line_ids, confidence}]` <br>- `subtotal` <br>- `tax` <br>- `total` <br>- `confidence` <br>- `timestamp_added` |
| **Receipt Validation**         | `IMAGE#<image_id>`       | `RECEIPT#<receipt_id>#ANALYSIS#VALIDATION`                              | `ANALYSIS_TYPE`         | `VALIDATION#<timestamp>`                                                        | `RECEIPT`              | `IMAGE#<image_id>#RECEIPT#<receipt_id>`                                         |                    |                                                       | - `business_identity: []` <br>- `address_verification: []` <br>- `phone_validation: []` <br>- `cross_field_consistency: []` <br>- `line_item_validation: []` <br>- `overall_valid` <br>- `timestamp_added` |
| **Places Cache**               | `PLACES#<search_type>`   | `VALUE#<search_value>`                                                  | `PLACE_ID`              | `PLACE_ID#<place_id>`                                                           | `LAST_USED`            | `<timestamp>`                                                                   |                    |                                                       | - `place_id` <br>- `places_response` <br>- `last_updated` <br>- `query_count` <br>- `confidence_score` <br>- `search_type` <br>- `search_value` <br>- `validation_status` |
| **Job**                        | `JOB#<job_id>`           | `JOB`                                                                   | `STATUS#<status>`       | `CREATED#<timestamp>`                                                           | `USER#<user>`          | `CREATED#<timestamp>`                                                           |                    |                                                       | - `name` <br>- `description` <br>- `created_at` <br>- `created_by` <br>- `status` <br>- `priority` <br>- `job_config` <br>- `estimated_duration` <br>- `tags` |
| **Job Status**                 | `JOB#<job_id>`           | `STATUS#<timestamp>`                                                    | `STATUS#<status>`       | `UPDATED#<timestamp>`                                                           |                        |                                                                                 |                    |                                                       | - `status` <br>- `progress` <br>- `message` <br>- `updated_at` <br>- `updated_by` <br>- `instance_id`                                                        |
| **Job Resource**               | `JOB#<job_id>`           | `RESOURCE#<resource_id>`                                                | `RESOURCE`              | `RESOURCE#<resource_id>`                                                        |                        |                                                                                 |                    |                                                       | - `resource_type` <br>- `instance_id` <br>- `instance_type` <br>- `gpu_count` <br>- `allocated_at` <br>- `released_at` <br>- `status`                        |
| **Job Metric**                 | `JOB#<job_id>`           | `METRIC#<metric_name>#<timestamp>`                                      | `METRIC#<metric_name>`  | `<timestamp>`                                                                   | `METRIC#<metric_name>` | `JOB#<job_id>#<timestamp>`                                                      |                    |                                                       | - `metric_name` <br>- `value` <br>- `unit` <br>- `timestamp` <br>- `step` <br>- `epoch`                                                                      |
| **Job Checkpoint**             | `JOB#<job_id>`           | `CHECKPOINT#<timestamp>`                                                | `CHECKPOINT`            | `JOB#<job_id>#<timestamp>`                                                      |                        |                                                                                 |                    |                                                       | - `s3_bucket` <br>- `s3_key` <br>- `size_bytes` <br>- `model_state` <br>- `optimizer_state` <br>- `metrics` <br>- `step` <br>- `epoch` <br>- `is_best`       |
| **Job Log**                    | `JOB#<job_id>`           | `LOG#<timestamp>`                                                       | `LOG`                   | `JOB#<job_id>#<timestamp>`                                                      |                        |                                                                                 |                    |                                                       | - `log_level` <br>- `message` <br>- `source` <br>- `exception`                                                                                               |
| **Job Dependency**             | `JOB#<job_id>`           | `DEPENDS_ON#<dependency_job_id>`                                        | `DEPENDENCY`            | `DEPENDENT#<job_id>#DEPENDENCY#<dependency_job_id>`                             | `DEPENDENCY`           | `DEPENDED_BY#<dependency_job_id>#DEPENDENT#<job_id>`                            |                    |                                                       | - `type` <br>- `condition` <br>- `created_at`                                                                                                                |
| **Instance**                   | `INSTANCE#<instance_id>` | `INSTANCE`                                                              | `STATUS#<status>`       | `INSTANCE#<instance_id>`                                                        |                        |                                                                                 |                    |                                                       | - `instance_type` <br>- `gpu_count` <br>- `status` <br>- `launched_at` <br>- `ip_address` <br>- `availability_zone` <br>- `is_spot` <br>- `health_status`    |
| **Instance Job**               | `INSTANCE#<instance_id>` | `JOB#<job_id>`                                                          | `JOB`                   | `JOB#<job_id>#INSTANCE#<instance_id>`                                           |                        |                                                                                 |                    |                                                       | - `assigned_at` <br>- `status` <br>- `resource_utilization`                                                                                                  |
| **Queue**                      | `QUEUE#<queue_name>`     | `QUEUE`                                                                 | `QUEUE`                 | `QUEUE#<queue_name>`                                                            |                        |                                                                                 |                    |                                                       | - `description` <br>- `created_at` <br>- `max_concurrent_jobs` <br>- `priority` <br>- `job_count`                                                            |
| **Queue Job**                  | `QUEUE#<queue_name>`     | `JOB#<job_id>`                                                          | `JOB`                   | `JOB#<job_id>#QUEUE#<queue_name>`                                               |                        |                                                                                 |                    |                                                       | - `enqueued_at` <br>- `priority` <br>- `position`                                                                                                            |

## Key Design Notes

1. **Unified Partition Key**:

   - All items related to an image (lines, words, word tags, receipts) share the same partition key (`PK = IMAGE#<image_id>`).

2. **Uppercased Tags**:

   - All tags are stored in **UPPERCASE** to enforce consistency and avoid issues with case sensitivity.
   - Example:
     - Word Tag: `TAG#FOOD#WORD#1`
     - Receipt-Word Tag: `TAG#FOOD#RECEIPT#1#WORD#1`

3. **Embedded Tags**:

   - The `Word` and `Receipt-Word` tables include a `tags` attribute, which is a list of all tags (in UPPERCASE) associated with them.
   - Example:
     - Word: `tags: ["FOOD", "DAIRY"]`
     - Receipt-Word: `tags: ["GROCERY", "DISCOUNTED"]`

4. **Scalable Many-to-Many Relationships**:
   - A tag can apply to multiple words or receipt-words, and a word or receipt-word can have multiple tags.

1. **Job-Centric Data Model**:
   - All items related to a specific job (status updates, resources, metrics, checkpoints, logs, dependencies) share the same partition key (`PK = JOB#<job_id>`).
   - This enables efficient queries for all information about a specific job.

2. **Status Tracking with GSI**:
   - GSI1 allows querying jobs by status (e.g., "find all running jobs" or "find all failed jobs").
   - Status updates are stored as separate items with timestamps in the sort key for complete history.

3. **Time-Based Sorting**:
   - Most sort keys include timestamps to maintain chronological order.
   - This enables queries like "get the most recent status update" or "get all metrics from the last hour".

4. **Job Dependencies**:
   - The Job Dependency item type tracks relationships between jobs.
   - GSI2 allows querying in both directions: "what jobs depend on job X?" and "what jobs does job X depend on?"

5. **Instance Management**:
   - The Instance and Instance Job item types track which instances are running which jobs.
   - This supports features like instance health monitoring and job reassignment.

6. **Queue Management**:
   - Queue and Queue Job item types manage the job queue.
   - Jobs can be organized into different queues with different priorities.

7. **Flexible Attributes**:
   - The `job_config` attribute in the Job item can store the complete job definition (YAML/JSON).
   - This allows for flexible job configurations without changing the table schema.

8. **Job Metric Comparison**:
   - GSI2 on Job Metric items enables efficient comparison of the same metric across different jobs.
   - Results are automatically grouped by job_id and then ordered by timestamp.
   - This supports use cases like comparing training loss or accuracy across multiple experiments.
