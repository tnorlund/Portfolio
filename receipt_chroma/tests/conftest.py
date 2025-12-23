"""
Root test fixtures for receipt_chroma package.

CHROMADB GLOBAL STATE RESET:
============================

ChromaDB's _system.stop() (called by PersistentClient.close()) corrupts global
Rust bindings state. This fixture reimports chromadb modules after each test
to clear this corruption, allowing subsequent tests to create new clients
successfully.

This fixture is auto-use, meaning it runs automatically after every test
without needing to be explicitly requested.

Reference: ChromaDB issue #5868
"""

import gc
import sys

import pytest


def _reset_chromadb_modules():
    """Reset ChromaDB global state by reimporting modules.

    ChromaDB's _system.stop() (called when closing PersistentClient) corrupts
    global Rust bindings state. This helper reimports chromadb modules to clear
    the corrupted state.

    IMPORTANT: Only reset 'chromadb' modules, not 'receipt_chroma' modules.
    Resetting receipt_chroma breaks import references in test files.
    """
    # Remove only chromadb modules from sys.modules (not receipt_chroma)
    chromadb_modules = [
        mod
        for mod in list(sys.modules.keys())
        if mod == "chromadb" or mod.startswith("chromadb.")
    ]
    for mod in chromadb_modules:
        del sys.modules[mod]

    # Force garbage collection to fully release resources
    gc.collect()


@pytest.fixture(autouse=True)
def reset_chromadb_state_after_test():
    """Auto-reset ChromaDB global state after each test.

    This fixture runs automatically after every test to clear any corrupted
    ChromaDB Rust bindings state. This prevents test pollution where one test's
    PersistentClient.close() corrupts the global state for subsequent tests.

    The reset happens AFTER the test completes (in the yield/teardown phase),
    so the test itself can use chromadb normally.
    """
    # Let the test run
    yield

    # After the test, reset chromadb modules to clear any corrupted state
    _reset_chromadb_modules()
