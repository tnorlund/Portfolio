import os
from receipt_dynamo import DynamoClient
from openai import OpenAI
from pinecone import Pinecone


def get_clients():
    dynamodb_table = os.environ["DYNAMO_TABLE_NAME"]
    pinecone_api_key = os.environ["PINECONE_API_KEY"]
    openai_api_key = os.environ["OPENAI_API_KEY"]

    dynamo_client = DynamoClient(dynamodb_table)
    openai_client = OpenAI(api_key=openai_api_key)
    pinecone = Pinecone(api_key=pinecone_api_key)
    pinecone_index = pinecone.Index(os.environ["PINECONE_INDEX_NAME"])

    return dynamo_client, openai_client, pinecone_index
