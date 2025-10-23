"""
Unit tests for ChromaDB operations in receipt_label package.

These tests use real ChromaDB operations to test actual business logic
rather than mocked functionality.
"""

import unittest
import tempfile
import os
from unittest.mock import MagicMock

import pytest

from receipt_label.utils.chroma_client import ChromaDBClient


class TestChromaDBOperations(unittest.TestCase):
    """Test real ChromaDB operations that the compaction lambda uses."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_chromadb_metadata_update(self):
        """Test real ChromaDB metadata update operations."""
        # Create real ChromaDB instance
        client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
        
        # Create collection and add test data
        collection = client.get_collection("test_words")
        collection.add(
            ids=["IMAGE#test#RECEIPT#00001#WORD#00001"],
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["Target"],
            metadatas=[{"merchant": "Target Store"}]
        )
        
        # Test real metadata update
        collection.update(
            ids=["IMAGE#test#RECEIPT#00001#WORD#00001"],
            metadatas=[{"merchant": "Target Store", "address": "123 Main St"}]
        )
        
        # Verify real changes
        results = collection.get(ids=["IMAGE#test#RECEIPT#00001#WORD#00001"])
        self.assertEqual(len(results["metadatas"]), 1)
        self.assertEqual(results["metadatas"][0]["merchant"], "Target Store")
        self.assertEqual(results["metadatas"][0]["address"], "123 Main St")
    
    def test_chromadb_id_construction_patterns(self):
        """Test ChromaDB ID construction patterns used by compaction lambda."""
        # Create real ChromaDB instance
        client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
        
        # Test the ID patterns that the compaction lambda constructs
        test_ids = [
            "IMAGE#550e8400-e29b-41d4-a716-446655440000#RECEIPT#00001#WORD#00001",
            "IMAGE#550e8400-e29b-41d4-a716-446655440000#RECEIPT#00001#WORD#00002",
            "IMAGE#550e8400-e29b-41d4-a716-446655440000#RECEIPT#00001#LINE#00001",
        ]
        
        collection = client.get_collection("test_words")
        
        # Add embeddings with these IDs
        collection.add(
            ids=test_ids,
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]],
            documents=["word1", "word2", "line1"],
            metadatas=[
                {"merchant": "Target", "word_id": 1},
                {"merchant": "Target", "word_id": 2},
                {"merchant": "Target", "line_id": 1}
            ]
        )
        
        # Test querying by specific IDs (what compaction lambda does)
        results = collection.get(ids=test_ids)
        self.assertEqual(len(results["ids"]), 3)
        
        # Verify all IDs are present
        for test_id in test_ids:
            self.assertIn(test_id, results["ids"])
        
        # Test metadata filtering (what compaction lambda does)
        # ChromaDB doesn't support $exists, so we'll test with actual values
        word_results = collection.get(
            where={"word_id": 1},  # Test with actual value
            include=["metadatas"]
        )
        self.assertEqual(len(word_results["ids"]), 1)  # Only one word with word_id=1
        
        line_results = collection.get(
            where={"line_id": 1},  # Test with actual value
            include=["metadatas"]
        )
        self.assertEqual(len(line_results["ids"]), 1)  # Only one line with line_id=1
    
    def test_chromadb_collection_management(self):
        """Test ChromaDB collection creation and management."""
        # Create real ChromaDB instance
        client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
        
        # Test collection creation
        collection = client.get_collection("test_collection")
        self.assertIsNotNone(collection)
        
        # Test adding data to collection
        collection.add(
            ids=["test_id_1", "test_id_2"],
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            documents=["doc1", "doc2"],
            metadatas=[{"field": "value1"}, {"field": "value2"}]
        )
        
        # Test querying collection
        results = collection.get(ids=["test_id_1"])
        self.assertEqual(len(results["ids"]), 1)
        self.assertEqual(results["ids"][0], "test_id_1")
        self.assertEqual(results["documents"][0], "doc1")
        self.assertEqual(results["metadatas"][0]["field"], "value1")
        
        # Test updating collection
        collection.update(
            ids=["test_id_1"],
            metadatas=[{"field": "updated_value"}]
        )
        
        # Verify update
        updated_results = collection.get(ids=["test_id_1"])
        self.assertEqual(updated_results["metadatas"][0]["field"], "updated_value")
        
        # Test deleting from collection
        collection.delete(ids=["test_id_2"])
        
        # Verify deletion
        remaining_results = collection.get()
        self.assertEqual(len(remaining_results["ids"]), 1)
        self.assertEqual(remaining_results["ids"][0], "test_id_1")
    
    def test_chromadb_error_handling(self):
        """Test ChromaDB error handling scenarios."""
        # Create real ChromaDB instance
        client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
        
        collection = client.get_collection("test_error_handling")
        
        # Test error handling for invalid operations
        with self.assertRaises(Exception):
            # Try to add with mismatched lengths (this should raise an error)
            collection.add(
                ids=["id1", "id2"],
                embeddings=[[0.1, 0.2]],  # Only one embedding for two IDs
                documents=["doc1", "doc2"],
                metadatas=[{"field": "value1"}, {"field": "value2"}]
            )
        
        # Test that valid operations work
        collection.add(
            ids=["valid_id"],
            embeddings=[[0.1, 0.2, 0.3]],
            documents=["valid_doc"],
            metadatas=[{"field": "valid_value"}]
        )
        
        # Verify the valid operation worked
        results = collection.get(ids=["valid_id"])
        self.assertEqual(len(results["ids"]), 1)
        self.assertEqual(results["ids"][0], "valid_id")
        
        # Test updating non-existent ID (ChromaDB might not raise an error, but we can verify it doesn't affect existing data)
        collection.update(
            ids=["non_existent_id"],
            metadatas=[{"field": "value"}]
        )
        
        # Verify existing data is still there
        results_after_update = collection.get(ids=["valid_id"])
        self.assertEqual(len(results_after_update["ids"]), 1)
        self.assertEqual(results_after_update["ids"][0], "valid_id")
    
    def test_chromadb_vector_operations(self):
        """Test ChromaDB vector operations (add, update, delete, query)."""
        # Create real ChromaDB instance
        client = ChromaDBClient(persist_directory=self.temp_dir, mode="snapshot")
        
        collection = client.get_collection("test_vector_ops")
        
        # Test adding vectors
        test_embeddings = [
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
            [0.9, 1.0, 1.1, 1.2]
        ]
        
        collection.add(
            ids=["vec1", "vec2", "vec3"],
            embeddings=test_embeddings,
            documents=["vector 1", "vector 2", "vector 3"],
            metadatas=[
                {"type": "word", "pos": 1},
                {"type": "word", "pos": 2},
                {"type": "line", "pos": 1}
            ]
        )
        
        # Test vector similarity search
        query_results = collection.query(
            query_embeddings=[[0.1, 0.2, 0.3, 0.4]],  # Similar to vec1
            n_results=2,
            include=["metadatas", "distances"]
        )
        
        # Should find vec1 as most similar
        self.assertEqual(len(query_results["ids"][0]), 2)
        self.assertEqual(query_results["ids"][0][0], "vec1")  # Most similar
        
        # Test updating vectors
        collection.update(
            ids=["vec1"],
            embeddings=[[0.15, 0.25, 0.35, 0.45]]  # Slightly different
        )
        
        # Verify vector was updated
        updated_results = collection.get(ids=["vec1"], include=["embeddings"])
        self.assertEqual(len(updated_results["embeddings"]), 1)
        # ChromaDB returns numpy arrays, so we need to convert to list for comparison
        import numpy as np
        expected_embedding = np.array([0.15, 0.25, 0.35, 0.45])
        actual_embedding = updated_results["embeddings"][0]
        np.testing.assert_array_almost_equal(actual_embedding, expected_embedding, decimal=5)
        
        # Test deleting vectors
        collection.delete(ids=["vec3"])
        
        # Verify deletion
        remaining_results = collection.get()
        self.assertEqual(len(remaining_results["ids"]), 2)
        self.assertNotIn("vec3", remaining_results["ids"])


if __name__ == '__main__':
    unittest.main()
