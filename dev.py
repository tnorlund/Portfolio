from collections import Counter
from dynamo import DynamoClient
import os

dynamo = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
# rwts = dynamo.listReceiptWordTags()  # Suppose this returns a list of objects
rwts = dynamo.getReceiptWordTags("address")
keys = [rwt.to_ReceiptWord_key() for rwt in rwts]
print(json.dumps(dict(letter), indent=4))

# # 9fa7233f7b9e6637141af1a4487bc20e1b833a7ab989fbfb3c7a7c6ff5d51d4d

rws = dynamo.getReceiptWordsByKeys(keys)
print(rws)