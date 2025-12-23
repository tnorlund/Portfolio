"""
Root test fixtures for receipt_chroma package.

NOTE: ChromaDB module resets are handled explicitly in integration tests that
need them, not via autouse fixtures. This prevents interference with unit tests
that use in-memory clients and run in parallel with pytest-xdist.

See tests/integration/conftest.py for the reset_chromadb_modules() helper.
"""

# No autouse fixtures here - integration tests handle their own cleanup
