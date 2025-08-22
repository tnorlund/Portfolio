# CodeRabbit Review Checklist — PR #333

## infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py

- [ ] **Delta handler must not ACK unprocessed deltas**
  - [ ] Return **partial-batch failure** body for each unhandled record:
    `{"batchItemFailures": [{"itemIdentifier": "<SequenceNumber>"}]}`
  - [ ] In Pulumi, set the event source mapping with
    `function_response_types=["ReportBatchItemFailures"]` (and optionally `bisect_batch_on_function_error=True`)
  - [ ] (Optional stopgap) Route delta path to legacy compactor instead of ACKing
  - [ ] Add alarms on sustained delta failures

## infra/chromadb_compaction/lambdas/stream_processor.py

- [ ] **Report sent count, not queued length** (around lines 324–343)
  - [ ] Use `sent_count` for `LambdaResponse.queued_messages`
  - [ ] Log `queued_messages=sent_count`
  - [ ] Update the corresponding metric to reflect actual sent messages

- [ ] **Fix structured logging call** (around lines 640–642)
  - [ ] Replace printf-style logger call with kwargs:
    `logger.error("Queue URL not found", queue_env_var=queue_env_var)`

## infra/chromadb_compaction/utils/logging.py

- [ ] **Don't fabricate `LogRecord`; use standard logger API**
  - [ ] Change `_log_with_context` signature to accept `*, exc_info=None, **kwargs`
  - [ ] Replace `makeRecord` + `handle` with:
    `self.logger.log(level, message, extra={"extra_fields": extra_fields}, exc_info=exc_info, stacklevel=3)`
  - [ ] Thread `exc_info` through public methods as needed (e.g., usage: `logger.error("...", error=str(e), exc_info=True)`)

## infra/chromadb_compaction/utils/metrics.py

- [ ] **Clean imports + use timezone-aware UTC**
  - [ ] Remove unused: `os`, `Any`, `List`
  - [ ] Import `timezone` and use `datetime.now(timezone.utc)` for CloudWatch timestamps

## infra/embedding_step_functions/infrastructure.py (+ components/monitoring.py)

- [ ] **Make Logs Insights queries dynamic**
  - [ ] Replace **hardcoded** CloudWatch log group names with a query builder that derives `SOURCE '...'` from the injected `lambda_functions` (e.g., `fn.logGroupName`)
  - [ ] Ensure dashboards/widgets work across stacks (dev/stage/prod)

## infra/embedding_step_functions/unified_embedding/handlers/find_unembedded.py

- [ ] **Use structured logger that supports kwargs**
  - [ ] Switch to `get_operation_logger` so calls like `logger.info("...", count=...)` don't raise
  - [ ] Apply same change to other callsites in the file (also noted at lines 52, 58, 74)

## infra/embedding_step_functions/unified_embedding/router.py

- [ ] **Use explicit relative imports for handlers**
  - [ ] Replace `from handlers import ...` with explicit relative imports

## infra/embedding_step_functions/unified_embedding/utils/tracing.py

- [ ] **Fail-open when no active X-Ray segment; guard cleanup**
  - [ ] Wrap `begin_subsegment` so that if no segment is present (common locally / when daemon off), tracing becomes a no-op instead of raising
  - [ ] Only call `end_subsegment()` if a subsegment was started
  - [ ] Guard `current_subsegment()` before `add_exception(e)` to avoid `NoneType` errors

## Optional follow-ups (nice to have)

- [ ] Add CloudWatch alarms for repeated delta redrives / failures
- [ ] Confirm any metrics names referenced in logs/dashboards match the updated ones (e.g., `StreamProcessorQueuedMessages` now reflects sent messages)
- [ ] Add unit tests for:
  - [ ] Partial-batch failure response structure
  - [ ] Logger adapters accepting kwargs without raising
  - [ ] Router imports resolution within the package
  - [ ] Tracing context manager behavior with/without X-Ray