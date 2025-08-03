# Pinecone to ChromaDB Migration Guide

This document outlines the migration from Pinecone to ChromaDB for the receipt_label package.

## Overview

We have successfully migrated the receipt_label package from using Pinecone (a SaaS vector database) to ChromaDB (a self-hosted vector database). This migration aligns with the broader architecture goals outlined in `CHROMA_DB_PLAN.md` to eliminate SaaS costs and vendor lock-in.

## What Changed

### 1. Dependencies
- **Removed**: `pinecone>=3.0` from `pyproject.toml`
- **Added**: `chromadb>=0.5.0` to `pyproject.toml`

### 2. New Modules
- **`receipt_label/utils/chroma_client.py`**: ChromaDB client management with collections for words and lines
- **`receipt_label/decision_engine/chroma_integration.py`**: ChromaDecisionHelper replacing PineconeDecisionHelper
- **`scripts/migrate_pinecone_to_chroma.py`**: Data migration script

### 3. Updated Modules
- **`receipt_label/utils/client_manager.py`**: Added ChromaDB support, deprecated Pinecone
- **`receipt_label/embedding/word/realtime.py`**: Uses ChromaDB for word embeddings
- **`receipt_label/embedding/line/realtime.py`**: Uses ChromaDB for line embeddings
- **`receipt_label/label_validation/utils.py`**: Updated ID generation function
- **`receipt_label/decision_engine/integration.py`**: Uses ChromaDecisionHelper

## Environment Variables

### Deprecated (but still supported for backward compatibility)
- `PINECONE_API_KEY`
- `PINECONE_INDEX_NAME`
- `PINECONE_HOST`

### New
- `CHROMA_PERSIST_PATH`: Path for ChromaDB persistence (optional, uses in-memory if not set)

## Migration Steps

### 1. Update Dependencies
```bash
cd receipt_label
pip install -e .  # This will install chromadb
```

### 2. Set Environment Variables
```bash
export CHROMA_PERSIST_PATH="/path/to/chroma/storage"  # Optional for persistence
```

### 3. Migrate Existing Data (if needed)
```bash
# Ensure you have both Pinecone credentials and ChromaDB path set
export PINECONE_API_KEY="your-api-key"
export PINECONE_INDEX_NAME="receipt-embeddings"
export PINECONE_HOST="your-pinecone-host"
export CHROMA_PERSIST_PATH="/path/to/chroma/storage"

# Run migration
python scripts/migrate_pinecone_to_chroma.py

# Verify migration
python scripts/migrate_pinecone_to_chroma.py --verify
```

### 4. Update Code References
The migration maintains backward compatibility through several mechanisms:

1. **Alias in utils**: `pinecone_id_from_label = chroma_id_from_label`
2. **ClientManager**: The `pinecone` property shows deprecation warning but still works if Pinecone is installed
3. **DecisionEngine**: `pinecone_helper` is an alias for `chroma_helper`

However, you should update your code to use the new names:
```python
# Old
from receipt_label.decision_engine import PineconeDecisionHelper
id = pinecone_id_from_label(label)

# New
from receipt_label.decision_engine import ChromaDecisionHelper
id = chroma_id_from_label(label)
```

## Architecture Benefits

1. **Cost Reduction**: No more Pinecone subscription fees
2. **Self-Hosted**: Full control over vector storage
3. **S3 Integration**: Can sync with S3 for backup/distribution (see CHROMA_DB_PLAN.md)
4. **Serverless Compatible**: Works with Lambda architecture

## Testing

Run the existing tests to ensure everything works:
```bash
cd receipt_label
pytest tests/
```

Note: Test fixtures will need to be updated to mock ChromaDB instead of Pinecone.

## Rollback Plan

If you need to rollback to Pinecone:

1. Reinstall pinecone: `pip install "pinecone>=3.0"`
2. Set Pinecone environment variables
3. The code still supports Pinecone through backward compatibility

## Next Steps

1. **Update test fixtures** to use ChromaDB mocks
2. **Remove Pinecone imports** after confirming migration success
3. **Implement S3 sync** for ChromaDB as outlined in CHROMA_DB_PLAN.md
4. **Update Lambda layers** to include ChromaDB instead of Pinecone

## Troubleshooting

### Import Errors
If you see import errors for ChromaDB:
```bash
pip install chromadb>=0.5.0
```

### Performance Issues
ChromaDB performance can be tuned by:
- Using persistent storage instead of in-memory
- Adjusting batch sizes for upserts
- Using appropriate distance metrics

### Data Integrity
Always verify migration with:
```bash
python scripts/migrate_pinecone_to_chroma.py --verify
```

This compares vector counts between Pinecone and ChromaDB to ensure completeness.