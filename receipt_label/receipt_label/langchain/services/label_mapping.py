from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_label.langchain.models import CurrencyLabel


def create_receipt_word_labels_from_currency_labels(
    discovered_labels: List[CurrencyLabel],
    lines: List[ReceiptLine],
    words: List[ReceiptWord] | None,
    image_id: str,
    receipt_id: str,
    client: DynamoClient,
) -> List[ReceiptWordLabel]:
    print(f"   🏷️ Creating ReceiptWordLabel entities...")

    actual_receipt_id = int(receipt_id.split("/")[-1])

    word_mapping = {}
    global_word_index: dict[str, List[tuple[int, int]]] = {}
    line_to_word_texts = {}
    word_text_map: dict[tuple, str] = {}

    if words is None:
        for line in lines:
            try:
                _words = client.list_receipt_words_from_line(
                    image_id=line.image_id,
                    receipt_id=line.receipt_id,
                    line_id=line.line_id,
                )
                print(
                    f"   🔎 Loaded {len(_words)} words for line {line.line_id}"
                )
                for word in _words:
                    key = (line.line_id, word.text.strip().upper())
                    if key not in word_mapping:
                        word_mapping[key] = []
                    word_mapping[key].append(word.word_id)
                    line_to_word_texts.setdefault(line.line_id, set()).add(
                        word.text.strip().upper()
                    )
                    word_text_map[(line.line_id, word.word_id)] = word.text
                    for token in word.text.strip().split():
                        token_key = (line.line_id, token.upper())
                        if token_key not in word_mapping:
                            word_mapping[token_key] = []
                        if word.word_id not in word_mapping[token_key]:
                            word_mapping[token_key].append(word.word_id)
                    t = word.text.strip().upper()
                    if t:
                        global_word_index.setdefault(t, []).append(
                            (line.line_id, word.word_id)
                        )
                        for tok in t.split():
                            global_word_index.setdefault(tok, []).append(
                                (line.line_id, word.word_id)
                            )
            except Exception as e:
                print(
                    f"   ⚠️ Warning: Could not load words for line {line.line_id}: {e}"
                )
                continue
    else:
        print(f"   🔎 Using preloaded words: {len(words)} total")
        for word in words:
            key = (word.line_id, word.text.strip().upper())
            if key not in word_mapping:
                word_mapping[key] = []
            word_mapping[key].append(word.word_id)
            line_to_word_texts.setdefault(word.line_id, set()).add(
                word.text.strip().upper()
            )
            word_text_map[(word.line_id, word.word_id)] = word.text
            for token in word.text.strip().split():
                token_key = (word.line_id, token.upper())
                if token_key not in word_mapping:
                    word_mapping[token_key] = []
                if word.word_id not in word_mapping[token_key]:
                    word_mapping[token_key].append(word.word_id)
            t = word.text.strip().upper()
            if t:
                global_word_index.setdefault(t, []).append(
                    (word.line_id, word.word_id)
                )
                for tok in t.split():
                    global_word_index.setdefault(tok, []).append(
                        (word.line_id, word.word_id)
                    )

    print(
        f"   📝 Built word mapping for {len(word_mapping)} unique (line_id, word_text) combinations"
    )

    receipt_word_labels = []
    current_time = datetime.now()

    for label in discovered_labels:
        label_type_str = (
            label.label_type.value
            if hasattr(label.label_type, "value")
            else str(label.label_type)
        )
        is_currency_label = label_type_str in {
            "GRAND_TOTAL",
            "TAX",
            "SUBTOTAL",
            "LINE_TOTAL",
        }

        if is_currency_label:
            if not getattr(label, "line_ids", None):
                print(
                    f"   ⚠️ Skipping currency label '{getattr(label, 'line_text', '')}' - no line_ids"
                )
                continue

            for line_id in getattr(label, "line_ids", []) or []:
                word_ids: List[int] = []
                amount_tokens: List[str] = []
                try:
                    amt = float(getattr(label, "amount"))
                    amt_str = f"{amt:.2f}".upper()
                    amount_tokens = [amt_str, f"${amt_str}"]
                except Exception:
                    amount_tokens = []

                for cand in amount_tokens:
                    ids = word_mapping.get((line_id, cand), [])
                    if ids:
                        word_ids.extend(ids)
                if not word_ids:
                    available = sorted(
                        list(line_to_word_texts.get(line_id, []))
                    )
                    sample = ", ".join(available[:10])
                    print(
                        f"   🔬 Debug: No amount-token match for {label_type_str} {getattr(label, 'amount', None)} on line {line_id}. Tried {amount_tokens} Available: [{sample}]"
                    )
                    print(
                        f"   ⚠️ No words found for currency label '{label_type_str}' on line {line_id}"
                    )
                    continue

                for word_id in word_ids:
                    receipt_word_label = ReceiptWordLabel(
                        image_id=image_id,
                        receipt_id=actual_receipt_id,
                        line_id=line_id,
                        word_id=word_id,
                        label=label.label_type.value,
                        reasoning=label.reasoning
                        or "Identified by simple_receipt_analyzer",
                        timestamp_added=current_time,
                        label_proposed_by="simple_receipt_analyzer",
                        validation_status="PENDING",
                    )
                    receipt_word_labels.append(receipt_word_label)
        else:
            candidate_texts = set()
            wt = str(getattr(label, "word_text", "")).strip().upper()
            if wt:
                candidate_texts.add(wt)
                for tok in wt.split():
                    candidate_texts.add(tok)

            matches: List[tuple[int, int]] = []
            for cand in candidate_texts:
                for loc in global_word_index.get(cand, []) or []:
                    matches.append(loc)

            if not matches and candidate_texts:
                for text_key, locations in global_word_index.items():
                    if any(text_key in cand for cand in candidate_texts):
                        matches.extend(locations)

            if not matches:
                print(
                    f"   ⚠️ No words found for line-item label '{label_type_str}' text='{wt}' across receipt"
                )
                continue

            for line_id, word_id in matches:
                receipt_word_label = ReceiptWordLabel(
                    image_id=image_id,
                    receipt_id=actual_receipt_id,
                    line_id=line_id,
                    word_id=word_id,
                    label=label.label_type.value,
                    reasoning=label.reasoning
                    or "Identified by simple_receipt_analyzer",
                    timestamp_added=current_time,
                    label_proposed_by="simple_receipt_analyzer",
                    validation_status="PENDING",
                )
                receipt_word_labels.append(receipt_word_label)

    print(
        f"   ✅ Created {len(receipt_word_labels)} ReceiptWordLabel entities"
    )
    return receipt_word_labels
