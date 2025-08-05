"""
S3-backed ChromaDB handler for serverless vector operations.

This handler persists ChromaDB collections to S3, allowing stateless
Lambda functions to work with persistent vector data.
"""
import json
import os
import tempfile
import shutil
from typing import Any, Dict, List, Optional
import boto3
import chromadb
from chromadb.config import Settings

s3_client = boto3.client('s3')
BUCKET_NAME = os.environ.get('CHROMA_S3_BUCKET')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ.get('DYNAMODB_TABLE_NAME'))


class S3ChromaDB:
    """ChromaDB wrapper that persists to S3."""
    
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.s3_prefix = f"chromadb/{collection_name}"
        self.local_path = f"/tmp/chroma_{collection_name}"
        
        # Download existing collection from S3 if it exists
        self._download_from_s3()
        
        # Initialize ChromaDB client
        self.client = chromadb.PersistentClient(
            path=self.local_path,
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            )
        )
    
    def _download_from_s3(self):
        """Download collection from S3 if it exists."""
        try:
            # List all objects with the collection prefix
            response = s3_client.list_objects_v2(
                Bucket=BUCKET_NAME,
                Prefix=self.s3_prefix
            )
            
            if 'Contents' not in response:
                return
            
            # Create local directory
            os.makedirs(self.local_path, exist_ok=True)
            
            # Download all files
            for obj in response['Contents']:
                key = obj['Key']
                local_file = key.replace(self.s3_prefix, self.local_path)
                os.makedirs(os.path.dirname(local_file), exist_ok=True)
                s3_client.download_file(BUCKET_NAME, key, local_file)
                
        except Exception as e:
            print(f"No existing collection found in S3: {e}")
    
    def _upload_to_s3(self):
        """Upload collection to S3."""
        for root, dirs, files in os.walk(self.local_path):
            for file in files:
                local_file = os.path.join(root, file)
                s3_key = local_file.replace(self.local_path, self.s3_prefix)
                s3_client.upload_file(local_file, BUCKET_NAME, s3_key)
    
    def get_or_create_collection(self, metadata: Optional[Dict] = None):
        """Get or create a collection."""
        try:
            collection = self.client.get_collection(self.collection_name)
        except:
            collection = self.client.create_collection(
                name=self.collection_name,
                metadata=metadata or {}
            )
        return collection
    
    def add_embeddings(self, ids: List[str], embeddings: List[List[float]], 
                      metadatas: List[Dict], documents: Optional[List[str]] = None):
        """Add embeddings to collection and persist to S3."""
        collection = self.get_or_create_collection()
        
        # Add to ChromaDB
        collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        
        # Persist to S3
        self._upload_to_s3()
        
        # Track in DynamoDB for querying
        for i, id_ in enumerate(ids):
            table.put_item(Item={
                'PK': f'VECTOR#{self.collection_name}',
                'SK': f'ID#{id_}',
                'TYPE': 'CHROMA_VECTOR',
                'collection_name': self.collection_name,
                'vector_id': id_,
                'metadata': metadatas[i],
                's3_prefix': self.s3_prefix,
            })
        
        return len(ids)
    
    def query(self, query_embeddings: List[List[float]], n_results: int = 10,
              where: Optional[Dict] = None):
        """Query similar vectors."""
        collection = self.get_or_create_collection()
        
        results = collection.query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where
        )
        
        return results
    
    def cleanup(self):
        """Clean up local files."""
        if os.path.exists(self.local_path):
            shutil.rmtree(self.local_path)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for S3-backed ChromaDB operations.
    
    This is designed to be invoked by your step function after 
    OpenAI batch processing completes.
    """
    operation = event.get('operation')
    
    if operation == 'batch_upsert':
        # This replaces upsert_line_embeddings_to_pinecone
        return batch_upsert_embeddings(event)
    elif operation == 'query':
        return query_embeddings(event)
    else:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': f'Unknown operation: {operation}'})
        }


def batch_upsert_embeddings(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Batch upsert embeddings from OpenAI batch results.
    This replaces the Pinecone upsert in your step function.
    """
    results = event['embedding_results']  # From OpenAI batch
    descriptions = event['receipt_descriptions']  # Receipt metadata
    collection_name = event.get('collection_name', 'receipt_lines')
    
    # Initialize S3-backed ChromaDB
    chroma_db = S3ChromaDB(collection_name)
    
    try:
        # Prepare data for ChromaDB
        ids = []
        embeddings = []
        metadatas = []
        documents = []
        
        for result in results:
            # Extract embedding data
            custom_id = result['custom_id']
            embedding = result['response']['body']['data'][0]['embedding']
            
            # Get metadata from descriptions
            parts = custom_id.split('#')
            image_id = parts[1]
            receipt_id = parts[3]
            line_id = parts[5]
            
            # Find matching description
            description = next(
                (d for d in descriptions 
                 if d['image_id'] == image_id and 
                    d['receipt_id'] == receipt_id and 
                    d['line_id'] == line_id),
                {}
            )
            
            # Prepare for ChromaDB
            ids.append(custom_id)
            embeddings.append(embedding)
            metadatas.append({
                'image_id': image_id,
                'receipt_id': receipt_id,
                'line_id': line_id,
                'text': description.get('text', ''),
                'merchant_name': description.get('merchant_name', ''),
                'timestamp': description.get('timestamp', ''),
            })
            documents.append(description.get('text', ''))
        
        # Batch upsert to ChromaDB
        upserted_count = chroma_db.add_embeddings(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
            documents=documents
        )
        
        return {
            'statusCode': 200,
            'upserted_count': upserted_count,
            'collection_name': collection_name,
            's3_location': f"s3://{BUCKET_NAME}/{chroma_db.s3_prefix}"
        }
        
    finally:
        # Clean up local files
        chroma_db.cleanup()


def query_embeddings(event: Dict[str, Any]) -> Dict[str, Any]:
    """Query similar embeddings."""
    collection_name = event.get('collection_name', 'receipt_lines')
    query_embeddings = event['query_embeddings']
    n_results = event.get('n_results', 10)
    where = event.get('where')
    
    chroma_db = S3ChromaDB(collection_name)
    
    try:
        results = chroma_db.query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where
        )
        
        return {
            'statusCode': 200,
            'results': {
                'ids': results['ids'],
                'distances': results['distances'],
                'metadatas': results['metadatas'],
                'documents': results.get('documents', [])
            }
        }
    finally:
        chroma_db.cleanup()