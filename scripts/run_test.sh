#!/bin/bash
# Script to run integration tests with required environment variables

export DYNAMO_TABLE_NAME=TestTable
export PINECONE_API_KEY=test-key
export OPENAI_API_KEY=test-key
export PINECONE_INDEX_NAME=test-index
export PINECONE_HOST=test-host

cd receipt_label
python -m pytest receipt_label/tests/test_labeler_integration.py -v
