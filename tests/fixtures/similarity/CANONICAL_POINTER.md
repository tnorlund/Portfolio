# Canonical similarity fixtures (live-captured)

The blessed Round A reference set is too large for git (142MB pretty-printed;
GitHub caps files at 100MB). It lives in S3, integrity-guarded by the loader's
content_sha256 check.

- s3 uri: s3://raw-image-bucket-c779c32/similarity-fixtures/canonical-2026-08-31/golden.json.gz
- sha256 (uncompressed golden.json): 199d7f4fc16858e1bf6aaea0a748edb6822145a4b2af6fa9078c6f5fd7420144
- size: 142,746,129 bytes (28.8MB gzipped)
- captured: 2026-08-31, Chroma Cloud dev (receipt_dev), 7m43s
- contents: 86 receipts, 258 queries, 4,209 corpus vectors; skips: 1 missing_vector, 7 receipt_not_found
- capture cmd: scripts/similarity_harness/capture_golden.py --extra-receipts <extras15> --canonical

Fetch before evaluating:
```
aws s3 cp s3://raw-image-bucket-c779c32/similarity-fixtures/canonical-2026-08-31/golden.json.gz - | gunzip > tests/fixtures/similarity/golden.json
```
The committed golden.json in this directory remains the small offline-bootstrap
set (canonical: false) used by unit tests. Known offline-replay ceiling vs this
canonical set: recall@10 ≈ 0.87 overall (corpus-truncation, documented in
FIXFORWARD.md follow-ups).
