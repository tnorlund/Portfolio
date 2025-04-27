from receipt_label.utils import get_clients

# 1) get your Pinecone index handle
_, _, pinecone_index = get_clients()

# 2) find your embedding dimension and build a zero vector
stats = pinecone_index.describe_index_stats().to_dict()
dim = stats["dimension"]
zero_vec = [0.0] * dim

# 3) query for any vector where the 2nd element of valid_labels (index 1) exists
resp = pinecone_index.query(
    vector=zero_vec,
    top_k=10000,  # max allowed
    namespace="words",
    filter={"valid_labels.1": {"$exists": True}},
    include_metadata=True,
)

print(f"Found {len(resp.matches)} vectors with ≥2 valid_labels:")
for match in resp.matches:
    labels = match.metadata.get("valid_labels", [])
    print(match.id, "→", labels)
