from collections import Counter
from dynamo import DynamoClient
import os

dynamo = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
# rwts = dynamo.listReceiptWordTags()  # Suppose this returns a list of objects
rwts = dynamo.getReceiptWordTags("address")
keys = [rwt.to_ReceiptWord_key() for rwt in rwts]

rws = dynamo.getReceiptWordsByKeys(keys)
print(rws)