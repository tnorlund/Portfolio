"""Offline test doubles for receipt embedding consumers."""

from receipt_embeddings.testing.fake_index import (
    FakeVectorIndex,
    golden_fixture_client,
)

__all__ = ["FakeVectorIndex", "golden_fixture_client"]
