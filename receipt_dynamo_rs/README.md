# receipt-dynamo (Rust)

Native Rust port of the Python `receipt_dynamo` package. It shares the same
single-table DynamoDB schema, key formats, and AttributeValue wire encoding so
Rust and Python can read and write the same items.

## Why Rust

Python spends most of its time on receipt-scale work in:

- zero-padded key construction (`RECEIPT#{id:05d}#LINE#{id:05d}#WORD#{id:05d}`)
- high-precision float serialization matching `Decimal.quantize(ROUND_HALF_UP)`
- batching words/labels into DynamoDB's 25-item write limit
- geometry transforms on every OCR token

Those paths are CPU-bound and allocation-heavy in CPython. This crate keeps
them in typed structs, pre-sized buffers, and an async client with retries,
circuit breaking, and concurrent batch chunks.

## Crate layout

| Module | Role |
|--------|------|
| `keys` | PK / SK / GSI construction matching the Python table design |
| `attr` | DynamoDB AttributeValue encoding, including Python-compatible floats |
| `entities` | Image, receipt, line/word/letter, labels, jobs, OCR jobs |
| `amounts` | Receipt currency parsing (`parse_receipt_amount`, grand-total heuristics) |
| `labels` | `CORE_LABELS` vocabulary and alias normalization |
| `geometry` | Bounding boxes, corners, affine / perspective transforms |
| `store` | `InMemoryStore` (tests) and optional AWS SDK store |
| `client` | Typed CRUD, GSI4 receipt-details query, 25-item batch writes |

## Usage

```rust
use receipt_dynamo::{InMemoryStore, ReceiptDynamo, ReceiptWord, ReceiptWordLabel};

#[tokio::main]
async fn main() -> receipt_dynamo::Result<()> {
    let db = ReceiptDynamo::memory();
    // db.put_entity(&word).await?;
    // let words = db.query_receipt_words(image_id, receipt_id).await?;
    Ok(())
}
```

AWS (requires the `aws` feature, on by default):

```rust
let db = receipt_dynamo::ReceiptDynamo::from_env("receipts").await?;
```

## Testing

```bash
# Fast unit + in-memory store tests (no AWS credentials)
cargo test --manifest-path receipt_dynamo_rs/Cargo.toml --no-default-features

# Include AWS SDK compile
cargo test --manifest-path receipt_dynamo_rs/Cargo.toml

# Hot-path benches
cargo bench --manifest-path receipt_dynamo_rs/Cargo.toml --no-default-features
```

Python wire-format compatibility tests run automatically when
`receipt_dynamo` is importable (repo `.venv`). They compare Rust `to_item()`
JSON with Python `to_item()` for the same fixtures.

## Compatibility notes

- UUID keys accept RFC-4122 versions 4 and 5 with variant `[89ab]`, matching
  `receipt_dynamo.entities.util.assert_valid_uuid`.
- Geometry numbers use 20 decimal places; receipt corners and angles use 18;
  confidence uses 2. Formatting follows Python `Decimal(str(value)).quantize`.
- New word labels should still be authored with `normalize_core_label`. Reads
  must accept historical labels outside `CORE_LABELS`.
