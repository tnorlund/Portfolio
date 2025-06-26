# DynamoDB Entities in Receipt Labeler

The `receipt_label` package relies on the `DynamoClient` mixin from `receipt_dynamo`. Many modules create, read or update DynamoDB items via this client. Examples of these operations are shown below.

## Batch Processing

### Listing and Completing Batches

Listing pending batches and marking them complete touches the `BatchSummary` table.

`poll_line_embedding_batch.py` lists pending `LINE_EMBEDDING` batches using `getBatchSummariesByStatus` and later updates each summary:

```python
summaries, lek = dynamo_client.getBatchSummariesByStatus(
    status="PENDING",
    batch_type=BatchType.LINE_EMBEDDING,
    ...
)
...
batch_summary = dynamo_client.getBatchSummary(batch_id)
batch_summary.status = "COMPLETED"
dynamo_client.updateBatchSummary(batch_summary)
```

### Completion Batch Processing

Completion batches insert `BatchSummary` rows and update `ReceiptWordLabel` items before submitting:

```python
def add_batch_summary(summary: BatchSummary, client_manager: ClientManager = None) -> None:
    """Write the BatchSummary entity to DynamoDB."""
    ...
    client_manager.dynamo.addBatchSummary(summary)

def update_label_validation_status(labels: list[ReceiptWordLabel], client_manager: ClientManager = None) -> None:
    for label in labels:
        label.validation_status = ValidationStatus.PENDING.value
    client_manager.dynamo.updateReceiptWordLabels(labels)
```

## Processing Completion Results

`completion/poll.py` updates labels and stores `CompletionBatchResult` records:

```python
for chunk in _chunk(labels_to_update, 25):
    client_manager.dynamo.updateReceiptWordLabels(chunk)

if labels_to_add:
    for chunk in _chunk(labels_to_add, 25):
        client_manager.dynamo.addReceiptWordLabels(chunk)

...
for chunk in _chunk(completion_records, 25):
    client_manager.dynamo.addCompletionBatchResults(chunk)
```

## Embedding Results

### Line Embedding

Line‑embedding polling stores `EmbeddingBatchResult` rows and updates line statuses:

```python
embedding_results.append(
    EmbeddingBatchResult(
        batch_id=batch_id,
        image_id=image_id,
        receipt_id=receipt_id,
        ...
    )
)
...
dynamo_client.addEmbeddingBatchResults(chunk)
...
dynamo_client.updateReceiptLines(lines)
```

### Word Embedding

Word embedding submission similarly writes `BatchSummary` records and updates `ReceiptWord` statuses:

```python
def add_batch_summary(summary: BatchSummary, client_manager: ClientManager = None) -> None:
    ...
    client_manager.dynamo.addBatchSummary(summary)

def update_word_embedding_status(words: list[ReceiptWord], client_manager: ClientManager = None) -> None:
    for word in words:
        word.embedding_status = EmbeddingStatus.PENDING.value
    client_manager.dynamo.updateReceiptWords(words)
```

## Merchant Validation Utilities

The merchant validation package retrieves and updates `ReceiptMetadata` entities:

```python
client_manager.dynamo.addReceiptMetadata(metadata)
...
for record in batch:
    client_manager.dynamo.updateReceiptMetadata(record)
```

## Places API Cache

`PlacesCache` entries are read and written when using Google Places:

```python
cached_item = self.client_manager.dynamo.getPlacesCache(search_type, search_value)
...
self.client_manager.dynamo.incrementQueryCount(cached_item)
...
self.client_manager.dynamo.addPlacesCache(cache_item)
```

## Summary of DynamoDB Entities

Based on the code, `receipt_label` interacts with these DynamoDB models:

| Entity / Table                                                             | Example Operations                                        |
| -------------------------------------------------------------------------- | --------------------------------------------------------- |
| `BatchSummary`                                                             | add, update, list pending batches                         |
| `CompletionBatchResult`                                                    | add completion results                                    |
| `EmbeddingBatchResult`                                                     | add line/word embedding results                           |
| `ReceiptWordLabel`                                                         | query by status, update batches of labels, add new labels |
| `ReceiptWord`                                                              | update embedding status                                   |
| `ReceiptLine`                                                              | update embedding status                                   |
| `ReceiptMetadata`                                                          | list, query by place_id, add and update records           |
| `Receipt`, `ReceiptLine`, `ReceiptWord`, `ReceiptLetter`, `ReceiptWordTag` | retrieved via getReceiptDetails for processing            |
| `PlacesCache`                                                              | cache Google Places lookups                               |

## Client Configuration

These operations are coordinated through the `DynamoClient` provided by `receipt_dynamo`, instantiated in `client_manager.py`:

```python
def dynamo(self) -> DynamoClient:
    if self._dynamo_client is None:
        self._dynamo_client = DynamoClient(self.config.dynamo_table)
    return self._dynamo_client
```

## Conclusion

The `receipt_label` package performs CRUD operations on a variety of receipt-related entities:

- **Batch summaries** - tracking processing status
- **Embedding results** - storing vector computation outcomes
- **Receipt components** - words, lines, labels, metadata
- **Caching artifacts** - Google Places API responses

These operations span across completion, embedding, merchant validation, and auxiliary modules, all coordinated through the centralized `DynamoClient`.
