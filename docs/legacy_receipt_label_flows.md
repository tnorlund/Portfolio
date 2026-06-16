## Legacy receipt_label Step Functions Removed

As part of retiring the old `receipt_label` package, the following Step Functions and Lambdas were deleted. They have been superseded by the newer `receipt_places`, `receipt_chroma`, and `receipt_agent` workflows.

- **ValidationPipeline** (`infra/validation_pipeline/`) - Batched OpenAI completion jobs that validated pending labels and updated Dynamo with completion results. Replaced by current receipt place workflows, Chroma-backed validation, and label evaluator checks.
- **ValidationByMerchant** (`infra/validation_by_merchant/`) - Grouped receipts by canonical merchant name and revalidated labels (merchant name, currency, date/time, phone, address) using receipt_label validators. Superseded by Google Places-backed receipt place correction and the newer label validation stages.
- **Create Labels** (`infra/create_labels_step_functions/`) - LangGraph-driven receipt labeling that saved PENDING labels to Dynamo. Current labeling comes from LayoutLM, ChromaDB, and label evaluator pipelines.
- **Currency Validation** (`infra/currency_validation_step_functions/`) - LangGraph currency validation for a single receipt. Replaced by current label evaluator financial checks and Chroma-backed validation.
- **Word Label Step Functions** (`infra/word_label_step_functions/` and legacy embedding simple lambdas) – Receipt-label-driven embedding submission/polling for words/lines. All embedding, batching, and realtime paths now live in `receipt_chroma` unified embedding handlers.
- **Receipt Processor Lambda** (`infra/lambda_functions/receipt_processor/handler/`) – Monolithic ReceiptLabeler-based end-to-end processing. Supplanted by enhanced receipt processing and agent-based pipelines.

These removals also eliminate the `receipt-label` Lambda layer and keep CodeBuild/Docker contexts aligned with the newer packages.
