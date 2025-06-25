# Version 1

The current pattern in receipt_label.utils.clients.get_clients() builds multiple clients from environment variables and returns them as a tuple:

def get_clients():
dynamodb_table = os.environ["DYNAMO_TABLE_NAME"]
pinecone_api_key = os.environ["PINECONE_API_KEY"]
openai_api_key = os.environ["OPENAI_API_KEY"]

    dynamo_client = DynamoClient(dynamodb_table)
    openai_client = OpenAI(api_key=openai_api_key)
    pinecone = Pinecone(api_key=pinecone_api_key)
    pinecone_index = pinecone.Index(
        os.environ["PINECONE_INDEX_NAME"], host=os.environ["PINECONE_HOST"]
    )

    return dynamo_client, openai_client, pinecone_index

Many modules call this function at import time and store the returned clients as module‑level globals, e.g. in poll_line_embedding_batch:

from receipt_label.utils import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

This pattern hard‑codes environment access inside the function and makes unit testing harder because clients are created as side effects of module import. It also encourages global state across modules.

Suggested Improvement
Create a Clients (or ClientManager) dataclass that initializes these clients once from configuration. Pass an instance of this class into functions or classes that need it. This avoids implicit global state and allows tests to supply mocked clients easily. For example:

@dataclass
class Clients:
dynamo_client: DynamoClient
openai_client: OpenAI
pinecone_index: pinecone.Index

def init_clients(cfg: Config) -> Clients:
return Clients(
DynamoClient(cfg.dynamo_table),
OpenAI(api_key=cfg.openai_api_key),
Pinecone(api_key=cfg.pinecone_api_key).Index(
cfg.pinecone_index_name, host=cfg.pinecone_host
),
)
Modules would then accept a Clients instance via their public functions or through an orchestrating service, improving testability and separation of concerns.

# Version 2

The helper in receipt_label/utils/clients.py reads environment variables and returns a tuple of initialized clients:

def get_clients():
dynamodb_table = os.environ["DYNAMO_TABLE_NAME"]
pinecone_api_key = os.environ["PINECONE_API_KEY"]
openai_api_key = os.environ["OPENAI_API_KEY"]

    dynamo_client = DynamoClient(dynamodb_table)
    openai_client = OpenAI(api_key=openai_api_key)
    pinecone = Pinecone(api_key=pinecone_api_key)
    pinecone_index = pinecone.Index(
        os.environ["PINECONE_INDEX_NAME"], host=os.environ["PINECONE_HOST"]
    )

    return dynamo_client, openai_client, pinecone_index

Several modules import this and instantiate clients at import time, e.g. in embedding/word/poll.py:

from receipt_label.utils import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()

The tests must patch this function to inject mocks:

def fake_get_clients():
return DynamoClient(table_name), fake_openai, None

mocker.patch.object(clients, "get_clients", fake_get_clients)

This pattern causes the library to initialize external dependencies when modules are imported, making tests more complex and tightly coupling runtime configuration to module import.

Suggested Improvement
Use a dedicated object to manage clients and inject it where needed, rather than retrieving them via a global helper. A dataclass (e.g., Clients) or a small ClientManager could be created once at application startup and passed to functions or classes that require access to DynamoDB, OpenAI, or Pinecone. This avoids side effects at import time and simplifies testing.

# Version 3

# Version 4

The current client management is centralized in receipt_label/utils/clients.py:

8 def get_clients():
9 dynamodb_table = os.environ["DYNAMO_TABLE_NAME"]
10 pinecone_api_key = os.environ["PINECONE_API_KEY"]
11 openai_api_key = os.environ["OPENAI_API_KEY"]
...
15 pinecone = Pinecone(api_key=pinecone_api_key)
16 pinecone_index = pinecone.Index(
17 os.environ["PINECONE_INDEX_NAME"], host=os.environ["PINECONE_HOST"]
18 )
19
20 return dynamo_client, openai_client, pinecone_index

Many modules import this function and assign the returned tuple to global variables (e.g. completion/submit.py):

27 from receipt_label.utils import get_clients
31 dynamo_client, openai_client = get_clients()[:2]

Tests then need to patch get_clients and these globals (see tests/conftest.py lines 128‑188) to inject mocks, which is verbose and brittle:

128 @pytest.fixture(autouse=True)
129 def patch_clients(mocker, dynamodb_table_and_s3_bucket):
...
145 # 1) Fake Dynamo + OpenAI in get_clients()
...
162 def fake_get_clients():
163 return DynamoClient(table_name), fake_openai, None
165 mocker.patch.object(clients, "get_clients", fake_get_clients)
...
170 mocker.patch.object(sb, "openai_client", fake_openai)
173 mocker.patch.object(poll_batch, "openai_client", fake_openai)

Suggested Improvement
Replace the tuple-returning get_clients with a typed container and pass it explicitly to functions that need it.
