from typing import Callable, Dict, Any, Optional

from receipt_label.vector_store import VectorClient


def upsert_embeddings(
    line_client: VectorClient,
    word_client: VectorClient,
    line_embed_fn: Callable,
    word_embed_fn: Callable,
    ctx: Dict[str, Any],
    merchant_name: Optional[str] = None,
) -> None:
    """Compute and upsert line/word embeddings for a receipt context."""
    if ctx.get("lines"):
        try:
            lp = line_embed_fn(ctx["lines"], merchant_name=merchant_name)
        except TypeError:
            lp = line_embed_fn(ctx["lines"])
        if lp:
            # Expect either list-of-tuples or dict payload; support list-of-tuples here
            if isinstance(lp, list):
                ids, embeddings, metadatas, documents = [], [], [], []
                for line, emb in lp:
                    vector_id = f"IMAGE#{line.image_id}#RECEIPT#{int(line.receipt_id):05d}#LINE#{int(line.line_id):05d}"
                    ids.append(vector_id)
                    embeddings.append(emb)
                    metadatas.append(
                        {
                            "image_id": line.image_id,
                            "receipt_id": str(line.receipt_id),
                            "line_id": line.line_id,
                            "embedding_type": "line",
                            "text": line.text,
                            "merchant_name": merchant_name or "",
                        }
                    )
                    documents.append(line.text)
                line_client.upsert_vectors(
                    collection_name="lines",
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=documents,
                )
            else:
                line_client.upsert_vectors(collection_name="lines", **lp)

    if ctx.get("words"):
        try:
            wp = word_embed_fn(ctx["words"], merchant_name=merchant_name)
        except TypeError:
            wp = word_embed_fn(ctx["words"])
        if wp:
            if isinstance(wp, list):
                ids, embeddings, metadatas, documents = [], [], [], []
                for word, emb in wp:
                    vector_id = f"IMAGE#{word.image_id}#RECEIPT#{int(word.receipt_id):05d}#LINE#{int(word.line_id):05d}#WORD#{int(word.word_id):05d}"
                    ids.append(vector_id)
                    embeddings.append(emb)
                    metadatas.append(
                        {
                            "image_id": word.image_id,
                            "receipt_id": str(word.receipt_id),
                            "line_id": word.line_id,
                            "word_id": word.word_id,
                            "embedding_type": "word",
                            "text": word.text,
                            "merchant_name": merchant_name or "",
                        }
                    )
                    documents.append(word.text)
                word_client.upsert_vectors(
                    collection_name="words",
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=documents,
                )
            else:
                word_client.upsert_vectors(collection_name="words", **wp)
