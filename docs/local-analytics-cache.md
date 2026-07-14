# Local analytics cache

`scripts/local_analytics_cache.py` mirrors the receipt data needed for local
analytics without repeatedly reading AWS:

- every DynamoDB item in a queryable SQLite file;
- native ChromaDB `lines` and `words` snapshots;
- every raw S3 image referenced by an Image or Receipt record.

The default cache root is `.cache/analytics/<env>/`, which is already ignored
by Git.

Sync requires AWS credentials plus either Pulumi access or explicit resource
names. Serving the Dynamo cache requires Docker (or an existing
DynamoDB-compatible endpoint passed with `serve --endpoint-url ... --no-docker`).

## Quick start

```bash
# Discover the dev resources from Pulumi and sync everything.
make analytics-cache ENV=dev

# Verify local integrity plus the Dynamo table identity/item count and exact
# Chroma snapshot pointers.
make analytics-cache-validate ENV=dev

# Start DynamoDB Local, hydrate it from the cache, and print client settings.
make analytics-cache-serve ENV=dev
```

The first sync performs a parallel DynamoDB scan, downloads both Chroma
snapshots concurrently, and downloads raw images with 32 workers. Later syncs
reuse unchanged Chroma versions and local images. Use `--refresh-images` when
you want an S3 `HEAD` check for every cached image.

Explicit resource names can be used without Pulumi:

```bash
python scripts/local_analytics_cache.py sync \
  --env dev \
  --table-name ReceiptsTable \
  --chroma-bucket receipt-chromadb-dev
```

## Use the existing clients

Chroma snapshots are stored in their native persistent format, so the existing
client opens them directly:

```python
from receipt_chroma import ChromaClient

with ChromaClient(
    persist_directory=".cache/analytics/dev/chroma/lines",
    mode="read",
) as lines:
    collection = lines.get_collection("lines")
    print(collection.count())
```

`serve` runs the official DynamoDB Local image, recreates the source table's
keys and GSIs, and imports the cached wire-format items. It skips the import
when the local service already matches the current cache.

```bash
python scripts/local_analytics_cache.py serve --env dev
export DYNAMODB_ENDPOINT_URL=http://127.0.0.1:8000
export DYNAMODB_TABLE_NAME=<the table printed by serve>
```

`DynamoClient` honors `DYNAMODB_ENDPOINT_URL`, so existing call sites do not
need a separate adapter:

```python
import os
from receipt_dynamo import DynamoClient

client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
images, next_key = client.list_images(limit=100)
```

The endpoint can also be explicit:

```python
client = DynamoClient(
    "ReceiptsTable",
    endpoint_url="http://127.0.0.1:8000",
)
```

Stop the managed container with:

```bash
python scripts/local_analytics_cache.py stop --env dev
```

## Fast analytics

The complete DynamoDB mirror remains available as SQLite for scans and ad-hoc
SQL that would be inefficient through the service API:

```bash
sqlite3 .cache/analytics/dev/dynamodb.sqlite3
```

```sql
SELECT entity_type, COUNT(*)
FROM dynamo_items
GROUP BY entity_type
ORDER BY COUNT(*) DESC;

SELECT image_id, json_extract(item_json, '$.merchant_name') AS merchant
FROM dynamo_items
WHERE entity_type = 'RECEIPT_METADATA';
```

`item_json` is analytics-friendly native JSON. `dynamodb_json` preserves the
exact DynamoDB wire representation used to hydrate DynamoDB Local. The
`images` and `receipts` views cover the common top-level entities, and the
`raw_images` table maps every S3 object to its local file.

## Validate and invalidate

```bash
# Quick local + remote validation (default).
python scripts/local_analytics_cache.py validate --env dev

# No AWS calls.
python scripts/local_analytics_cache.py validate --env dev --local-only

# Also HEAD every raw image in parallel.
python scripts/local_analytics_cache.py validate --env dev --deep

# Instant metadata-only invalidation; files remain reusable by the next sync.
python scripts/local_analytics_cache.py invalidate --env dev

# Invalidate only one component.
python scripts/local_analytics_cache.py invalidate \
  --env dev --components chroma

# Delete data as well as invalidating it.
python scripts/local_analytics_cache.py invalidate --env dev --purge
```

The Chroma version check is exact because it compares the S3 atomic snapshot
pointers. DynamoDB does not expose a cheap content version: quick validation
checks the table ARN and its approximate `DescribeTable` item count. A sync is
the authoritative refresh and detects updates that do not change the item
count.
