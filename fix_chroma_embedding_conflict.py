#!/usr/bin/env python3
"""
Fix ChromaDB embedding function conflict.

This script resolves the issue where an existing ChromaDB collection was created 
with a "default" embedding function but the code is trying to use it with an 
"openai" embedding function.

The script provides two options:
1. Reset the existing collection (destroys data)
2. Create a new collection with the correct embedding function
"""

import os
from pathlib import Path
import shutil

LOCAL_CHROMA_WORD_PATH = Path(__file__).parent / "dev.word_chroma"
LOCAL_CHROMA_LINE_PATH = Path(__file__).parent / "dev.line_chroma"

def fix_embedding_conflict():
    """Fix the embedding function conflict by resetting ChromaDB collections."""
    
    print("ChromaDB Embedding Function Conflict Fix")
    print("=" * 50)
    
    # Check if collections exist
    word_exists = LOCAL_CHROMA_WORD_PATH.exists()
    line_exists = LOCAL_CHROMA_LINE_PATH.exists()
    
    print(f"Word ChromaDB exists: {word_exists}")
    print(f"Line ChromaDB exists: {line_exists}")
    
    if not word_exists and not line_exists:
        print("No existing ChromaDB collections found. Nothing to fix.")
        return
    
    print("\nThe existing ChromaDB collections were created with 'default' embedding function,")
    print("but your code is trying to use 'openai' embedding function.")
    print("\nOptions:")
    print("1. Delete existing collections and re-download from S3 (RECOMMENDED)")
    print("2. Keep existing collections and modify code to use default embeddings")
    print("3. Cancel (no changes)")
    
    choice = input("\nChoose option (1/2/3): ").strip()
    
    if choice == "1":
        # Delete existing collections
        if word_exists:
            print(f"Deleting {LOCAL_CHROMA_WORD_PATH}")
            shutil.rmtree(LOCAL_CHROMA_WORD_PATH)
        
        if line_exists:
            print(f"Deleting {LOCAL_CHROMA_LINE_PATH}")
            shutil.rmtree(LOCAL_CHROMA_LINE_PATH)
        
        print("\nDeleted existing ChromaDB collections.")
        print("When you run assemble_prompt.py again, it will re-download from S3")
        print("with the correct OpenAI embedding function.")
        
    elif choice == "2":
        print("\nTo use the existing collections with default embeddings,")
        print("modify the ChromaDBClient initialization in assemble_prompt.py:")
        print("\nChange:")
        print("  chroma_word_client = ChromaDBClient(")
        print("      persist_directory=str(LOCAL_CHROMA_WORD_PATH), mode=\"read\"")
        print("  )")
        print("\nTo:")
        print("  chroma_word_client = ChromaDBClient(")
        print("      persist_directory=str(LOCAL_CHROMA_WORD_PATH), mode=\"read\", metadata_only=True")
        print("  )")
        print("\nThis will use the default embedding function instead of OpenAI.")
        
    elif choice == "3":
        print("No changes made.")
        
    else:
        print("Invalid choice. No changes made.")

if __name__ == "__main__":
    fix_embedding_conflict()