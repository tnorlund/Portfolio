# ChromaDB Compaction Memory Optimization Analysis

## Problem Statement

The ChromaDB compaction Lambda function experiences high memory usage, consuming up to 7.8GB out of 8GB allocated memory, causing failures during word embedding processing. This document analyzes the root causes and proposes optimizations.

## Current Architecture

### Data Flow
1. **Polling Lambda** → Creates delta files in S3 (each containing 100-300 embeddings)
2. **Split into Chunks** → Groups 25 deltas per chunk for parallel processing
3. **Compaction Lambda** → Processes each chunk, merging deltas into ChromaDB
4. **Final Merge** → Combines all chunks into a single snapshot

### Memory Usage Breakdown

#### Per Embedding Storage
- **Vector**: 1536 dimensions × 4 bytes (float32) = 6,144 bytes
- **Document**: ~100-200 bytes of text
- **Metadata**: ~200-500 bytes of JSON (labels, bounding boxes, etc.)
- **Total per embedding**: ~7KB

#### Chunk 5 Example (Failure Case)
- **1,856 word embeddings** across 10 delta files
- Raw data: ~13MB
- ChromaDB overhead: ~200MB per instance
- HNSW index construction: ~100MB
- Multiple copies in memory: 3-4x multiplication
- **Peak usage**: 7,785 MB / 8,192 MB

## Root Causes of High Memory Usage

### 1. Multiple ChromaDB Instances

```python
# Current implementation creates multiple instances simultaneously
chroma_client = chromadb.PersistentClient(path=temp_dir)  # Main instance

for delta in deltas:
    delta_client = chromadb.PersistentClient(path=delta_temp)  # Delta instance
    # Both instances remain in memory
```

### 2. Full Data Loading

```python
# Loads ALL embeddings into memory at once
results = delta_collection.get(
    include=["embeddings", "documents", "metadatas"]
)
# This creates a large Python dictionary with all data
```

### 3. ChromaDB Internal Overhead

- **SQLite database**: Metadata storage with WAL (Write-Ahead Logging)
- **HNSW index**: Graph-based similarity search structure
- **Parquet files**: Columnar storage with compression buffers
- **DuckDB**: Query engine with result caching

### 4. No Explicit Cleanup

```python
# ChromaDB instances aren't explicitly closed
# Python garbage collection is delayed
# Temporary directories accumulate
```

## Proposed Solution: Sequential Processing

### Implementation Strategy

```python
def process_chunk_deltas_sequential(
    batch_id: str,
    chunk_index: int,
    chunk_deltas: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Process deltas sequentially to minimize memory usage.
    
    Key optimizations:
    1. Process one delta at a time
    2. Explicitly close ChromaDB instances
    3. Force garbage collection between deltas
    4. Stream embeddings in batches
    """
    
    bucket = os.environ["CHROMADB_BUCKET"]
    temp_dir = tempfile.mkdtemp()
    total_embeddings = 0
    
    try:
        # Initialize main ChromaDB once
        chroma_client = chromadb.PersistentClient(path=temp_dir)
        
        # Group deltas by collection
        deltas_by_collection = {}
        for delta in chunk_deltas:
            collection = delta.get("collection", "receipt_words")
            if collection not in deltas_by_collection:
                deltas_by_collection[collection] = []
            deltas_by_collection[collection].append(delta)
        
        # Process each collection's deltas sequentially
        for collection_name, collection_deltas in deltas_by_collection.items():
            # Get or create collection once
            try:
                collection = chroma_client.get_collection(collection_name)
            except:
                collection = chroma_client.create_collection(collection_name)
            
            # Process deltas one at a time
            for i, delta in enumerate(collection_deltas):
                logger.info(f"Processing delta {i+1}/{len(collection_deltas)}")
                
                # Process single delta
                embeddings_added = process_single_delta_optimized(
                    bucket=bucket,
                    delta_key=delta["delta_key"],
                    collection=collection,
                    batch_size=100  # Stream in small batches
                )
                
                total_embeddings += embeddings_added
                
                # Aggressive cleanup after each delta
                if i % 3 == 0:  # Every 3 deltas
                    import gc
                    gc.collect()
                    logger.info(f"Memory cleanup performed after delta {i+1}")
        
        # Save intermediate result
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        upload_to_s3(temp_dir, bucket, intermediate_key)
        
        return {
            "intermediate_key": intermediate_key,
            "embeddings_processed": total_embeddings,
        }
        
    finally:
        # Explicit cleanup
        if chroma_client:
            del chroma_client
        shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()


def process_single_delta_optimized(
    bucket: str,
    delta_key: str,
    collection: Any,
    batch_size: int = 100
) -> int:
    """
    Process a single delta with minimal memory footprint.
    """
    delta_temp = tempfile.mkdtemp()
    
    try:
        # Download delta
        download_from_s3(bucket, delta_key, delta_temp)
        
        # Open delta ChromaDB
        delta_client = chromadb.PersistentClient(path=delta_temp)
        delta_collection = delta_client.list_collections()[0]
        
        # Get total count
        total_count = delta_collection.count()
        
        if total_count == 0:
            return 0
        
        # Process in batches instead of loading all at once
        processed = 0
        
        # First, get all IDs (lightweight)
        all_ids = delta_collection.get(include=[])["ids"]
        
        # Process in batches
        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i:i + batch_size]
            
            # Get batch of embeddings
            batch_results = delta_collection.get(
                ids=batch_ids,
                include=["embeddings", "documents", "metadatas"]
            )
            
            # Upsert batch
            collection.upsert(
                ids=batch_results["ids"],
                embeddings=batch_results["embeddings"],
                documents=batch_results["documents"],
                metadatas=batch_results["metadatas"]
            )
            
            processed += len(batch_results["ids"])
            
            # Clear batch from memory
            del batch_results
        
        return processed
        
    finally:
        # Explicit cleanup
        if delta_client:
            del delta_client
        shutil.rmtree(delta_temp, ignore_errors=True)
```

## Memory Usage Comparison

### Current Implementation
- **Peak Memory**: 7.8 GB
- **Concurrent ChromaDB instances**: 2-3
- **Data copies in memory**: 3-4x
- **Garbage collection**: Delayed

### Optimized Implementation
- **Expected Peak Memory**: 2-3 GB
- **Concurrent ChromaDB instances**: 1-2 max
- **Data copies in memory**: 1-2x
- **Garbage collection**: Forced after each delta

## Additional Optimizations

### 1. Dynamic Chunk Sizing
```python
# Use smaller chunks for word embeddings
CHUNK_SIZE_WORDS = 15  # Reduced from 25
CHUNK_SIZE_LINES = 25  # Keep current size

def get_chunk_size(batch_type: str) -> int:
    return CHUNK_SIZE_WORDS if batch_type == "word" else CHUNK_SIZE_LINES
```

### 2. Memory Monitoring
```python
import psutil
import os

def log_memory_usage(stage: str):
    process = psutil.Process(os.getpid())
    memory_mb = process.memory_info().rss / 1024 / 1024
    logger.info(f"Memory usage at {stage}: {memory_mb:.2f} MB")
```

### 3. ChromaDB Configuration
```python
# Configure ChromaDB for lower memory usage
chroma_client = chromadb.PersistentClient(
    path=temp_dir,
    settings=Settings(
        anonymized_telemetry=False,
        persist_directory=temp_dir,
        # Reduce cache sizes
        chroma_cache_size=100,  # Reduced from default
    )
)
```

### 4. Parallel vs Sequential Trade-off
```python
def should_use_sequential(delta_count: int, avg_embeddings: int) -> bool:
    """
    Decide whether to use sequential processing based on data volume.
    """
    total_embeddings = delta_count * avg_embeddings
    
    # Use sequential for large batches to avoid memory issues
    if total_embeddings > 1500:
        return True
    
    # Use parallel for small batches for speed
    return False
```

## Performance Impact

### Sequential Processing
- **Pros**: 
  - 60-70% reduction in memory usage
  - More predictable memory patterns
  - Fewer OOM failures
- **Cons**:
  - 20-30% slower processing time
  - Less CPU utilization

### Recommended Configuration

For production deployment:

```python
# Lambda configuration
LAMBDA_CONFIG = {
    "embedding-vector-compact": {
        "memory": 6144,  # Can reduce from 10GB to 6GB with optimizations
        "timeout": 900,
        "ephemeral_storage": 10240,
        "environment": {
            "BATCH_SIZE": "100",
            "USE_SEQUENTIAL": "true",
            "FORCE_GC_INTERVAL": "3"
        }
    }
}
```

## Monitoring and Alerting

### Key Metrics to Track
1. **Memory Usage Percentage**: Alert if > 80%
2. **Processing Time per Delta**: Track degradation
3. **Embeddings per Second**: Measure throughput
4. **GC Frequency**: Monitor cleanup effectiveness

### CloudWatch Alarms
```python
# Add custom metrics
import boto3
cloudwatch = boto3.client('cloudwatch')

def publish_memory_metric(memory_percent: float):
    cloudwatch.put_metric_data(
        Namespace='ChromaDB/Compaction',
        MetricData=[
            {
                'MetricName': 'MemoryUsagePercent',
                'Value': memory_percent,
                'Unit': 'Percent'
            }
        ]
    )
```

## Implementation Plan

### Phase 1: Quick Wins (Immediate)
- [x] Increase Lambda memory to 10GB
- [ ] Add garbage collection calls
- [ ] Reduce chunk size for words

### Phase 2: Sequential Processing (1 week)
- [ ] Implement sequential delta processing
- [ ] Add memory monitoring
- [ ] Test with production workloads

### Phase 3: Advanced Optimizations (2 weeks)
- [ ] Implement streaming for large deltas
- [ ] Add dynamic chunk sizing
- [ ] Optimize ChromaDB configuration

## Testing Strategy

### Load Testing
```bash
# Generate test data with varying sizes
python scripts/generate_test_deltas.py --count 30 --size large

# Run compaction with monitoring
python scripts/test_compaction_memory.py --profile memory
```

### Memory Profiling
```python
from memory_profiler import profile

@profile
def test_compaction_memory():
    # Run compaction with memory profiling
    pass
```

## Conclusion

The high memory usage in ChromaDB compaction is primarily due to:
1. Multiple concurrent ChromaDB instances
2. Loading entire deltas into memory
3. Lack of explicit cleanup

The proposed sequential processing approach can reduce memory usage by 60-70% with minimal performance impact. This would allow reducing Lambda memory allocation from 10GB to 6GB, resulting in cost savings while improving reliability.

## References

- [ChromaDB Memory Management](https://docs.trychroma.com/production/performance)
- [AWS Lambda Memory Optimization](https://docs.aws.amazon.com/lambda/latest/dg/configuration-memory.html)
- [Python Memory Profiling](https://github.com/pythonprofilers/memory_profiler)
- [HNSW Algorithm Memory Complexity](https://arxiv.org/abs/1603.09320)