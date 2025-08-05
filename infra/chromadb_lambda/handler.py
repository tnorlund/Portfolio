"""
ChromaDB Lambda handler for vector operations.

This handler provides a serverless interface to ChromaDB operations,
storing vectors in ephemeral storage with optional DynamoDB persistence.
"""
import json
import os
import chromadb
from chromadb.config import Settings
import boto3
from typing import Any, Dict, List, Optional

# Initialize ChromaDB client with ephemeral storage
# Note: /tmp is the only writable directory in Lambda
chroma_client = chromadb.Client(Settings(
    chroma_db_impl="duckdb+parquet",
    persist_directory="/tmp/chroma",
    anonymized_telemetry=False,
))

# Optional: DynamoDB client for persistent metadata
dynamodb = boto3.resource('dynamodb')
table_name = os.environ.get('DYNAMODB_TABLE_NAME')
table = dynamodb.Table(table_name) if table_name else None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for ChromaDB operations.
    
    Supports operations:
    - create_collection: Create a new collection
    - add: Add embeddings to a collection
    - query: Query similar vectors
    - delete_collection: Remove a collection
    """
    try:
        operation = event.get('operation')
        
        if operation == 'create_collection':
            return create_collection(event)
        elif operation == 'add':
            return add_embeddings(event)
        elif operation == 'query':
            return query_embeddings(event)
        elif operation == 'delete_collection':
            return delete_collection(event)
        elif operation == 'list_collections':
            return list_collections()
        else:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Unknown operation: {operation}',
                    'supported_operations': [
                        'create_collection', 'add', 'query', 
                        'delete_collection', 'list_collections'
                    ]
                })
            }
            
    except Exception as e:
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'type': type(e).__name__
            })
        }


def create_collection(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new ChromaDB collection."""
    collection_name = event['collection_name']
    metadata = event.get('metadata', {})
    
    # Create collection in ChromaDB
    collection = chroma_client.create_collection(
        name=collection_name,
        metadata=metadata
    )
    
    # Optionally persist metadata to DynamoDB
    if table:
        table.put_item(Item={
            'PK': f'CHROMA_COLLECTION#{collection_name}',
            'SK': 'METADATA',
            'TYPE': 'CHROMA_COLLECTION',
            'collection_name': collection_name,
            'metadata': metadata,
        })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'collection_name': collection_name,
            'status': 'created'
        })
    }


def add_embeddings(event: Dict[str, Any]) -> Dict[str, Any]:
    """Add embeddings to a collection."""
    collection_name = event['collection_name']
    
    # Get or create collection
    try:
        collection = chroma_client.get_collection(collection_name)
    except:
        collection = chroma_client.create_collection(collection_name)
    
    # Add embeddings
    ids = event['ids']
    embeddings = event['embeddings']
    metadatas = event.get('metadatas', [{}] * len(ids))
    documents = event.get('documents', None)
    
    collection.add(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents
    )
    
    # Optionally track in DynamoDB
    if table:
        for idx, id_ in enumerate(ids):
            table.put_item(Item={
                'PK': f'CHROMA_VECTOR#{collection_name}',
                'SK': f'ID#{id_}',
                'TYPE': 'CHROMA_VECTOR',
                'collection_name': collection_name,
                'vector_id': id_,
                'metadata': metadatas[idx] if idx < len(metadatas) else {},
            })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'collection_name': collection_name,
            'added_count': len(ids)
        })
    }


def query_embeddings(event: Dict[str, Any]) -> Dict[str, Any]:
    """Query similar embeddings."""
    collection_name = event['collection_name']
    query_embeddings = event['query_embeddings']
    n_results = event.get('n_results', 10)
    where = event.get('where', None)
    where_document = event.get('where_document', None)
    
    collection = chroma_client.get_collection(collection_name)
    
    results = collection.query(
        query_embeddings=query_embeddings,
        n_results=n_results,
        where=where,
        where_document=where_document
    )
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'ids': results['ids'],
            'distances': results['distances'],
            'metadatas': results['metadatas'],
            'documents': results.get('documents', [])
        })
    }


def delete_collection(event: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a collection."""
    collection_name = event['collection_name']
    
    chroma_client.delete_collection(collection_name)
    
    # Clean up DynamoDB entries if using persistence
    if table:
        # This is simplified - in production you'd want pagination
        response = table.query(
            KeyConditionExpression='PK = :pk',
            ExpressionAttributeValues={
                ':pk': f'CHROMA_COLLECTION#{collection_name}'
            }
        )
        
        with table.batch_writer() as batch:
            for item in response.get('Items', []):
                batch.delete_item(Key={
                    'PK': item['PK'],
                    'SK': item['SK']
                })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'collection_name': collection_name,
            'status': 'deleted'
        })
    }


def list_collections() -> Dict[str, Any]:
    """List all collections."""
    collections = chroma_client.list_collections()
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'collections': [
                {
                    'name': col.name,
                    'metadata': col.metadata
                } for col in collections
            ]
        })
    }