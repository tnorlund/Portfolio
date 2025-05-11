import re

from receipt_dynamo.entities import ReceiptWordLabel


def pinecone_id_from_label(label: ReceiptWordLabel) -> str:
    return (
        f"IMAGE#{label.image_id}#"
        f"RECEIPT#{label.receipt_id:05d}#"
        f"LINE#{label.line_id:05d}#"
        f"WORD#{label.word_id:05d}"
    )


def normalize_text(text: str) -> str:
    return re.sub(r"[^\w\s\-]", "", text.lower().strip())
