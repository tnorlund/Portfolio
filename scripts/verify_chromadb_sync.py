#!/usr/bin/env python3
"""
CLI script for ChromaDB sync verification.

This script implements the functionality described in GitHub issue #334:
fast comparison of local and S3 ChromaDB snapshots using hash comparison.

Examples:
    # Check S3 hash only
    python verify_chromadb_sync.py --database words --bucket my-bucket

    # Compare local directory with S3
    python verify_chromadb_sync.py --database words --bucket my-bucket --local /tmp/chromadb

    # Download and compare if local doesn't exist
    python verify_chromadb_sync.py --database words --bucket my-bucket --local /tmp/chromadb --download
"""

import argparse
import os
import sys
from pathlib import Path

# Add the receipt_label module to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "receipt_label"))


def main():
    parser = argparse.ArgumentParser(
        description="Verify ChromaDB sync between local directory and S3 snapshot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--database", "-d",
        required=True,
        help="Database name (e.g., 'words', 'lines')"
    )
    
    parser.add_argument(
        "--bucket", "-b", 
        required=True,
        help="S3 bucket name"
    )
    
    parser.add_argument(
        "--local", "-l",
        help="Local ChromaDB directory path (optional)"
    )
    
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download S3 snapshot for comparison if local path doesn't exist"
    )
    
    parser.add_argument(
        "--algorithm",
        default="md5",
        choices=["md5", "sha1", "sha256", "sha512"],
        help="Hash algorithm to use (default: md5)"
    )
    
    parser.add_argument(
        "--region",
        help="AWS region (optional)"
    )
    
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output final result"
    )
    
    args = parser.parse_args()
    
    try:
        from receipt_label.utils.chroma_s3_helpers import verify_chromadb_sync
        
        if not args.quiet:
            print(f"🔍 Verifying ChromaDB sync for database '{args.database}'")
            print(f"📦 S3 Bucket: {args.bucket}")
            if args.local:
                print(f"📁 Local Path: {args.local}")
            print(f"🔢 Algorithm: {args.algorithm.upper()}")
            print()
        
        # Perform verification
        result = verify_chromadb_sync(
            database=args.database,
            bucket=args.bucket,
            local_path=args.local,
            download_for_comparison=args.download,
            hash_algorithm=args.algorithm,
            region=args.region
        )
        
        # Display results
        status = result["status"]
        
        if status == "error":
            print(f"❌ Error: {result['error']}")
            print(f"💡 Recommendation: {result['recommendation']}")
            return 1
            
        elif status == "s3_only":
            print("📊 S3 Snapshot Info:")
            print(f"   Hash: {result['s3_hash']}")
            print(f"   Files: {result['s3_file_count']}")
            print(f"   Size: {result['s3_size_bytes']:,} bytes")
            print(f"   Algorithm: {result['algorithm'].upper()}")
            print(f"💡 Recommendation: {result['recommendation']}")
            return 0
            
        elif status == "identical":
            if not args.quiet:
                print("✅ IDENTICAL - Local directory matches S3 snapshot")
                print(f"   Hash: {result['local_hash']}")
                print(f"   Files: {result['local_file_count']}")
                print(f"   Size: {result['local_size_bytes']:,} bytes")
                print(f"   Comparison time: {result['comparison_time_seconds']:.3f}s")
            else:
                print("✅ Identical")
            return 0
            
        elif status == "different":
            print("❌ DIFFERENT - Local directory differs from S3 snapshot")
            print(f"   Local hash:  {result['local_hash']}")
            print(f"   S3 hash:     {result['s3_hash']}")
            print(f"   Local files: {result['local_file_count']}")
            print(f"   S3 files:    {result['s3_file_count']}")
            print(f"   Local size:  {result['local_size_bytes']:,} bytes")
            print(f"   S3 size:     {result['s3_size_bytes']:,} bytes")
            print(f"   Message: {result['message']}")
            print(f"   Comparison time: {result['comparison_time_seconds']:.3f}s")
            print(f"💡 Recommendation: {result['recommendation']}")
            return 2
            
        else:
            print(f"❓ Unknown status: {status}")
            return 3
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("💡 Make sure receipt_label package is properly installed")
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())