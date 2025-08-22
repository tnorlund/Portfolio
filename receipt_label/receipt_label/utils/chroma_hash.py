"""
ChromaDB hash calculation utilities for snapshot verification.

Provides deterministic hash calculation for ChromaDB directories to enable
fast comparison between local and S3 snapshots without downloading entire datasets.

This implementation follows the pattern from GitHub issue #334:
find . -type f -print0 | sort -z | xargs -0 md5sum | md5sum | cut -d' ' -f1
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChromaDBHashResult:
    """Result of ChromaDB directory hash calculation."""
    
    directory_hash: str
    file_count: int
    total_size_bytes: int
    hash_algorithm: str = "md5"
    calculation_time_seconds: float = 0.0
    files_processed: List[str] = field(default_factory=list)
    calculated_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "directory_hash": self.directory_hash,
            "file_count": self.file_count,
            "total_size_bytes": self.total_size_bytes,
            "hash_algorithm": self.hash_algorithm,
            "calculation_time_seconds": self.calculation_time_seconds,
            "files_processed": self.files_processed,
            "calculated_at": self.calculated_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "ChromaDBHashResult":
        """Create instance from dictionary."""
        return cls(**data)


def calculate_chromadb_hash(
    directory_path: str,
    algorithm: str = "md5",
    exclude_patterns: Optional[List[str]] = None,
    include_file_list: bool = True
) -> ChromaDBHashResult:
    """
    Calculate deterministic hash of ChromaDB directory.
    
    This implementation replicates the bash command from the GitHub issue:
    find . -type f -print0 | sort -z | xargs -0 md5sum | md5sum | cut -d' ' -f1
    
    Args:
        directory_path: Path to ChromaDB directory
        algorithm: Hash algorithm to use ("md5", "sha256", "sha1")
        exclude_patterns: File patterns to exclude (e.g., ["*.tmp", "*.log"])
        include_file_list: Whether to include processed file list in result
        
    Returns:
        ChromaDBHashResult with hash and metadata
        
    Raises:
        ValueError: If directory doesn't exist or algorithm is unsupported
        OSError: If directory cannot be read
    """
    start_time = time.time()
    
    # Validate inputs
    if not os.path.exists(directory_path):
        raise ValueError(f"Directory does not exist: {directory_path}")
    
    if not os.path.isdir(directory_path):
        raise ValueError(f"Path is not a directory: {directory_path}")
    
    # Validate algorithm
    supported_algorithms = ["md5", "sha1", "sha256", "sha512"]
    if algorithm not in supported_algorithms:
        raise ValueError(f"Unsupported algorithm: {algorithm}. Use one of: {supported_algorithms}")
    
    logger.info("Calculating %s hash for directory: %s", algorithm.upper(), directory_path)
    
    # Get all files in directory, sorted for deterministic results
    try:
        all_files = []
        total_size = 0
        
        for root, _, files in os.walk(directory_path):
            for file in files:
                file_path = os.path.join(root, file)
                
                # Skip files matching exclude patterns
                if exclude_patterns:
                    relative_path = os.path.relpath(file_path, directory_path)
                    if any(_matches_pattern(relative_path, pattern) for pattern in exclude_patterns):
                        logger.debug("Excluding file: %s", relative_path)
                        continue
                
                # Only process regular files
                if os.path.isfile(file_path):
                    # Calculate relative path for deterministic sorting
                    relative_path = os.path.relpath(file_path, directory_path)
                    file_size = os.path.getsize(file_path)
                    
                    all_files.append((relative_path, file_path, file_size))
                    total_size += file_size
        
        # Sort files by relative path for deterministic ordering
        all_files.sort(key=lambda x: x[0])
        
        logger.info("Found %d files, total size: %d bytes", len(all_files), total_size)
        
        # Calculate individual file hashes and combine them
        hash_obj = hashlib.new(algorithm)
        processed_files = []
        
        for relative_path, full_path, file_size in all_files:
            try:
                # Calculate hash of individual file
                file_hash = _calculate_file_hash(full_path, algorithm)
                
                # Combine relative path and file hash for deterministic result
                # This replicates the "md5sum" output format: "hash  filename"
                hash_line = f"{file_hash}  {relative_path}\n"
                hash_obj.update(hash_line.encode('utf-8'))
                
                if include_file_list:
                    processed_files.append(relative_path)
                
                logger.debug("Processed file: %s (hash: %s)", relative_path, file_hash[:8])
                
            except (OSError, IOError) as e:
                logger.warning("Skipping unreadable file %s: %s", relative_path, e)
                continue
        
        # Final hash is the hash of all individual file hashes
        directory_hash = hash_obj.hexdigest()
        calculation_time = time.time() - start_time
        
        logger.info(
            "Calculated %s hash: %s (processed %d files in %.2f seconds)",
            algorithm.upper(),
            directory_hash,
            len(processed_files),
            calculation_time
        )
        
        return ChromaDBHashResult(
            directory_hash=directory_hash,
            file_count=len(processed_files),
            total_size_bytes=total_size,
            hash_algorithm=algorithm,
            calculation_time_seconds=calculation_time,
            files_processed=processed_files if include_file_list else [],
            calculated_at=datetime.now(timezone.utc).isoformat()
        )
        
    except (OSError, IOError) as e:
        logger.error("Error reading directory %s: %s", directory_path, e)
        raise


def _calculate_file_hash(file_path: str, algorithm: str = "md5") -> str:
    """Calculate hash of a single file."""
    hash_obj = hashlib.new(algorithm)
    
    with open(file_path, 'rb') as f:
        # Read file in chunks to handle large files efficiently
        while chunk := f.read(8192):
            hash_obj.update(chunk)
    
    return hash_obj.hexdigest()


def _matches_pattern(file_path: str, pattern: str) -> bool:
    """Check if file path matches exclude pattern (simple wildcard support)."""
    import fnmatch
    return fnmatch.fnmatch(file_path, pattern)


def create_hash_file_content(hash_result: ChromaDBHashResult) -> str:
    """
    Create JSON content for .snapshot_hash file.
    
    Args:
        hash_result: Hash calculation result
        
    Returns:
        JSON string for storage in S3
    """
    content = {
        "directory_hash": hash_result.directory_hash,
        "algorithm": hash_result.hash_algorithm,
        "file_count": hash_result.file_count,
        "total_size_bytes": hash_result.total_size_bytes,
        "calculated_at": hash_result.calculated_at,
        "calculation_time_seconds": hash_result.calculation_time_seconds,
        "version": "1.0"  # For future compatibility
    }
    
    return json.dumps(content, indent=2)


def parse_hash_file_content(content: str) -> ChromaDBHashResult:
    """
    Parse JSON content from .snapshot_hash file.
    
    Args:
        content: JSON string from S3
        
    Returns:
        ChromaDBHashResult object
        
    Raises:
        ValueError: If content is invalid JSON or missing required fields
    """
    try:
        data = json.loads(content)
        
        # Validate required fields
        required_fields = ["directory_hash", "algorithm", "file_count", "total_size_bytes"]
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            raise ValueError(f"Missing required fields in hash file: {missing_fields}")
        
        return ChromaDBHashResult(
            directory_hash=data["directory_hash"],
            file_count=data["file_count"],
            total_size_bytes=data["total_size_bytes"],
            hash_algorithm=data["algorithm"],
            calculation_time_seconds=data.get("calculation_time_seconds", 0.0),
            calculated_at=data.get("calculated_at"),
            files_processed=[]  # Not stored in hash file to save space
        )
        
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in hash file: {e}")


def compare_hash_results(
    local_result: ChromaDBHashResult,
    s3_result: ChromaDBHashResult,
    strict_algorithm_check: bool = True
) -> Dict[str, any]:
    """
    Compare two hash results for consistency.
    
    Args:
        local_result: Hash result from local directory
        s3_result: Hash result from S3 hash file
        strict_algorithm_check: Whether to require matching algorithms
        
    Returns:
        Dictionary with comparison results
    """
    comparison = {
        "hashes_match": local_result.directory_hash == s3_result.directory_hash,
        "local_hash": local_result.directory_hash,
        "s3_hash": s3_result.directory_hash,
        "local_file_count": local_result.file_count,
        "s3_file_count": s3_result.file_count,
        "file_counts_match": local_result.file_count == s3_result.file_count,
        "local_size": local_result.total_size_bytes,
        "s3_size": s3_result.total_size_bytes,
        "sizes_match": local_result.total_size_bytes == s3_result.total_size_bytes,
        "algorithms_match": local_result.hash_algorithm == s3_result.hash_algorithm
    }
    
    # Overall result
    if strict_algorithm_check:
        comparison["is_identical"] = (
            comparison["hashes_match"] and
            comparison["file_counts_match"] and
            comparison["sizes_match"] and
            comparison["algorithms_match"]
        )
    else:
        comparison["is_identical"] = (
            comparison["hashes_match"] and
            comparison["file_counts_match"] and
            comparison["sizes_match"]
        )
    
    # Recommendation
    if comparison["is_identical"]:
        comparison["recommendation"] = "up_to_date"
        comparison["message"] = "Local directory matches S3 snapshot"
    else:
        comparison["recommendation"] = "sync_needed"
        
        # Provide specific reason for mismatch
        if not comparison["hashes_match"]:
            comparison["message"] = "Content differs between local and S3"
        elif not comparison["file_counts_match"]:
            comparison["message"] = f"File count mismatch: local={local_result.file_count}, s3={s3_result.file_count}"
        elif not comparison["sizes_match"]:
            comparison["message"] = f"Size mismatch: local={local_result.total_size_bytes}, s3={s3_result.total_size_bytes}"
        elif strict_algorithm_check and not comparison["algorithms_match"]:
            comparison["message"] = f"Algorithm mismatch: local={local_result.hash_algorithm}, s3={s3_result.hash_algorithm}"
        else:
            comparison["message"] = "Unknown difference detected"
    
    return comparison