# receipt_embeddings

Backend-agnostic vector search for receipt embeddings (part of the Chroma
removal — see `docs/chroma-removal/SPEC.md`).

- `receipt_embeddings.vector_client` — the `VectorSearchClient` protocol
  (`search()` / `get_vector()`) and `ScoredItem`. Dependency-free; consumers
  import only this. `chromadb` must never appear in this package.
- `receipt_embeddings.testing` — `FakeVectorIndex`, a deterministic exact
  cosine-NN in-memory backend for unit tests (needs the `testing` extra:
  `pip install 'receipt_embeddings[testing]'`).

All backends return **cosine distance** (`1 - cosine similarity`, lower is
closer), so thresholds tuned on Chroma carry to DynamoDB unchanged.
