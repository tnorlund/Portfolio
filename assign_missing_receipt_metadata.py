#!/usr/bin/env python3
import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.constants import ValidationMethod, MerchantValidationStatus

# Shared normalization utilities
from receipt_label.merchant_validation.normalize import (
    build_full_address_from_lines,
    build_full_address_from_words,
    normalize_phone,
    normalize_url,
    normalize_address,
)
from receipt_label.utils.text_reconstruction import ReceiptTextReconstructor
from receipt_label.vector_store import VectorClient
from receipt_label.data.places_api import PlacesAPI
from openai import OpenAI


# -----------------------------------------------------------------------------
# Inputs and environment
# -----------------------------------------------------------------------------
ROOT = Path(__file__).parent
# Ensure local package import works when not installed in editable mode
sys.path.insert(0, str(ROOT / "receipt_label"))
WORDS_EXPORT = ROOT / "dev.words.ndjson"
LINES_EXPORT = ROOT / "dev.lines.ndjson"

# Load environment and secrets will be done in main() after parsing args


# -----------------------------------------------------------------------------
# IO helpers
# -----------------------------------------------------------------------------


def _load_words_lines(
    client: DynamoClient,
) -> Tuple[List[ReceiptWord], List[ReceiptLine]]:
    if not WORDS_EXPORT.exists() or not LINES_EXPORT.exists():
        words, _ = client.list_receipt_words()
        lines, _ = client.list_receipt_lines()
        if WORDS_EXPORT.exists():
            WORDS_EXPORT.unlink()
        with open(WORDS_EXPORT, "w", encoding="utf-8") as wf:
            for w in words:
                wf.write(json.dumps(dict(w)) + "\n")
        if LINES_EXPORT.exists():
            LINES_EXPORT.unlink()
        with open(LINES_EXPORT, "w", encoding="utf-8") as lf:
            for ln in lines:
                lf.write(json.dumps(dict(ln)) + "\n")
        return words, lines

    words: List[ReceiptWord] = []
    lines: List[ReceiptLine] = []
    with open(WORDS_EXPORT, "r", encoding="utf-8") as wf:
        for line in wf:
            words.append(ReceiptWord(**json.loads(line)))
    with open(LINES_EXPORT, "r", encoding="utf-8") as lf:
        for line in lf:
            lines.append(ReceiptLine(**json.loads(line)))
    return words, lines


# -----------------------------------------------------------------------------
# Clustering by normalized anchors
# -----------------------------------------------------------------------------
ReceiptKey = Tuple[str, int]


def _addresses_by_receipt(
    words: List[ReceiptWord],
    lines: List[ReceiptLine],
) -> Dict[ReceiptKey, str]:
    # Prefer address values from anchor words if present; otherwise build from lines
    words_by_receipt: Dict[ReceiptKey, List[ReceiptWord]] = defaultdict(list)
    lines_by_receipt: Dict[ReceiptKey, List[ReceiptLine]] = defaultdict(list)

    for w in words:
        rk = (str(w.image_id), int(w.receipt_id))
        words_by_receipt[rk].append(w)
    for ln in lines:
        rk = (str(ln.image_id), int(ln.receipt_id))
        lines_by_receipt[rk].append(ln)

    results: Dict[ReceiptKey, str] = {}
    for rk, wlist in words_by_receipt.items():
        addr = build_full_address_from_words(wlist)
        if not addr:
            llist = sorted(
                lines_by_receipt.get(rk, []), key=lambda l: int(l.line_id)
            )
            addr = build_full_address_from_lines(llist)
        results[rk] = addr
    # Include any receipts that only have lines present
    for rk, llist in lines_by_receipt.items():
        if rk not in results:
            results[rk] = build_full_address_from_lines(
                sorted(llist, key=lambda l: int(l.line_id))
            )
    return results


def _phones_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "phone":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        digits = normalize_phone(
            str(w.extracted_data.get("value") or w.text or "")
        )
        if digits:
            results[rk].add(digits)
    return results


def _urls_by_receipt(words: List[ReceiptWord]) -> Dict[ReceiptKey, Set[str]]:
    results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
    for w in words:
        if not w.extracted_data or w.extracted_data.get("type") != "url":
            continue
        rk = (str(w.image_id), int(w.receipt_id))
        u = normalize_url(str(w.extracted_data.get("value") or w.text or ""))
        if u:
            results[rk].add(u)
    return results


# -----------------------------------------------------------------------------
# Metadata selection within clusters
# -----------------------------------------------------------------------------


def _fetch_existing_metadata_map(
    client: DynamoClient, indices: Iterable[ReceiptKey]
) -> Dict[ReceiptKey, ReceiptMetadata]:
    index_list = list({(img, rec) for img, rec in indices})
    if not index_list:
        return {}
    # Batch to avoid per-receipt fetch cost
    metadatas = client.get_receipt_metadatas_by_indices(index_list)
    return {(m.image_id, int(m.receipt_id)): m for m in metadatas}


def _choose_canonical_metadata(
    metas: List[ReceiptMetadata],
) -> Optional[ReceiptMetadata]:
    if not metas:
        return None

    # Heuristic: prefer MATCHED, then canonical fields, then matched_fields size
    def score(m: ReceiptMetadata) -> Tuple[int, int, int, str]:
        is_matched = int(
            (m.validation_status or "").upper()
            == MerchantValidationStatus.MATCHED.value
        )
        has_canonical = int(
            bool(
                m.canonical_place_id
                or m.canonical_address
                or m.canonical_phone_number
            )
        )
        matched_count = len(m.matched_fields or [])
        return (
            is_matched,
            has_canonical,
            matched_count,
            m.merchant_name or "",
        )

    return sorted(metas, key=score, reverse=True)[0]


def _find_business_name_near_address(
    rk: ReceiptKey, receipt_words: List, lines: List
) -> Optional[str]:
    """Find business name using spatial context around the address.

    Args:
        rk: Receipt key (image_id, receipt_id)
        receipt_words: List of receipt words
        lines: List of receipt lines

    Returns:
        Business name if found, None otherwise
    """
    # Find the address line by looking for words with extracted_data type "address"
    address_line_id = None
    for word in receipt_words:
        extracted_data = getattr(word, "extracted_data", None)
        if extracted_data and extracted_data.get("type") == "address":
            address_line_id = getattr(word, "line_id", None)
            break

    if address_line_id is None:
        return None

    # Look at lines around the address (typically business name is above the address)
    search_lines = []
    for line in lines:
        line_id = getattr(line, "line_id", None)
        if (
            line_id is not None and abs(line_id - address_line_id) <= 2
        ):  # ±2 lines
            search_lines.append(line)

    # Sort by line_id to maintain order
    search_lines.sort(key=lambda x: getattr(x, "line_id", 0))

    # Collect non-noise text from lines around the address (without pattern detection)
    text_parts = []
    for line in search_lines:
        line_text = getattr(line, "text", "").strip()
        is_noise = getattr(line, "is_noise", False)

        # Skip empty lines and noise lines
        if not line_text or is_noise:
            continue

        text_parts.append(line_text)

    # Join the text parts and check query length limit
    if text_parts:
        combined_text = " ".join(text_parts)
        # Google Places API has a query length limit of ~2000 characters
        # Let's be conservative and limit to 1000 characters
        if len(combined_text) <= 1000:
            return combined_text
        else:
            # If too long, try to truncate intelligently
            # Take the first few parts that fit within limit
            truncated_parts = []
            current_length = 0
            for part in text_parts:
                if current_length + len(part) + 1 <= 1000:  # +1 for space
                    truncated_parts.append(part)
                    current_length += len(part) + 1
                else:
                    break
            if truncated_parts:
                return " ".join(truncated_parts)

    return None


def _build_receipt_metadata_from_places_api(
    places_result: Dict, target: ReceiptKey, search_type: str, query: str
) -> ReceiptMetadata:
    """Create ReceiptMetadata from Google Places API result.

    Args:
        places_result: Google Places API response
        target: Receipt key (image_id, receipt_id)
        search_type: Type of search ("address" or "phone")
        query: The original query used for the search

    Returns:
        ReceiptMetadata object populated from Places API data
    """
    image_id, receipt_id = target

    return ReceiptMetadata(
        image_id=str(image_id),
        receipt_id=int(receipt_id),
        place_id=places_result.get("place_id", ""),
        merchant_name=places_result.get("name", ""),
        matched_fields=[f"google_places_{search_type}"],
        timestamp=datetime.now(timezone.utc),
        merchant_category="",  # Not available in basic Places API response
        address=places_result.get("formatted_address", ""),
        phone_number="",  # Would need additional Places Details API call
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=f"Google Places API match via {search_type} search: {query}",
        canonical_place_id=places_result.get("place_id", ""),
        canonical_merchant_name=places_result.get("name", ""),
        canonical_address=places_result.get("formatted_address", ""),
        canonical_phone_number="",  # Would need additional Places Details API call
        validation_status="",
    )


def _build_proposed_from_canonical(
    canonical: ReceiptMetadata, target: ReceiptKey, matched_reasons: List[str]
) -> ReceiptMetadata:
    image_id, receipt_id = target
    return ReceiptMetadata(
        image_id=str(image_id),
        receipt_id=int(receipt_id),
        place_id=canonical.canonical_place_id or canonical.place_id or "",
        merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        matched_fields=list(
            set([*(canonical.matched_fields or []), *matched_reasons])
        ),
        timestamp=datetime.now(timezone.utc),
        merchant_category=canonical.merchant_category or "",
        address=canonical.canonical_address or canonical.address or "",
        phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validated_by=ValidationMethod.INFERENCE.value,
        reasoning=(
            "Derived from cluster neighbor with existing metadata; "
            + ", ".join(sorted(set(matched_reasons)))
        ),
        canonical_place_id=canonical.canonical_place_id
        or canonical.place_id
        or "",
        canonical_merchant_name=canonical.canonical_merchant_name
        or canonical.merchant_name
        or "",
        canonical_address=canonical.canonical_address
        or canonical.address
        or "",
        canonical_phone_number=canonical.canonical_phone_number
        or canonical.phone_number
        or "",
        validation_status="",
    )


# -----------------------------------------------------------------------------
# Main workflow
# -----------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find similar receipts by clustering normalized address/phone/url and "
            "propose ReceiptMetadata for receipts lacking metadata."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="If set, write proposed ReceiptMetadata records to DynamoDB (batch).",
    )
    parser.add_argument(
        "--max-write",
        type=int,
        default=0,
        help="Optional cap on number of proposed records to write (0 means no cap).",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Focus on specific receipts only. Format: IMAGE_ID:RECEIPT_ID. Can be repeated.",
    )
    parser.add_argument(
        "--include-existing",
        action="store_true",
        help="With --only, include receipts that already have metadata for analysis/logs.",
    )
    parser.add_argument(
        "--similarity-report",
        action="store_true",
        help="Run street-first and phone-line similarity over receipts lacking metadata and print a summary.",
    )
    parser.add_argument(
        "--report-path",
        type=str,
        default=str((ROOT / "reports/similarity_report.json").resolve()),
        help="File path to write full similarity report JSON.",
    )
    parser.add_argument(
        "--use-places-api",
        action="store_true",
        help="Enable Google Places API fallback for receipts that don't match via vector similarity.",
    )
    parser.add_argument(
        "--env",
        type=str,
        default="dev",
        choices=["dev", "prod"],
        help="Pulumi environment to use for API keys (default: dev).",
    )
    parser.add_argument(
        "--write-places-api",
        action="store_true",
        help="Write ReceiptMetadata created from Google Places API results to DynamoDB.",
    )
    parser.add_argument(
        "--max-places-api-calls",
        type=int,
        default=0,
        help="Maximum number of Google Places API calls to make (0 = no limit). Use to control costs.",
    )
    args = parser.parse_args()

    # Load environment and secrets based on specified environment
    # Change to infra directory for Pulumi commands
    original_cwd = os.getcwd()
    infra_dir = ROOT / "infra"
    if infra_dir.exists():
        os.chdir(infra_dir)

    try:
        env = load_env(args.env)
        secrets = load_secrets(args.env)
    finally:
        os.chdir(original_cwd)

    DDB_TABLE = env.get("dynamodb_table_name")

    if not DDB_TABLE:
        raise ValueError(
            f"No dynamodb_table_name configured in environment '{args.env}'"
        )

    # Set environment variable for PlacesAPI client
    os.environ["DYNAMODB_TABLE_NAME"] = DDB_TABLE

    client = DynamoClient(DDB_TABLE)

    words, lines = _load_words_lines(client)

    # Build quick indexes
    words_by_receipt: Dict[ReceiptKey, List[ReceiptWord]] = defaultdict(list)
    for w in words:
        words_by_receipt[(str(w.image_id), int(w.receipt_id))].append(w)
    line_text_by_key: Dict[Tuple[str, int, int], str] = {}
    for ln in lines:
        line_text_by_key[
            (str(ln.image_id), int(ln.receipt_id), int(ln.line_id))
        ] = str(ln.text or "")
    # also build lines_by_key for quick text reconstruction
    lines_by_key: Dict[ReceiptKey, List[ReceiptLine]] = defaultdict(list)
    for ln in lines:
        lines_by_key[(str(ln.image_id), int(ln.receipt_id))].append(ln)

    # Text reconstruction helpers available to all paths
    reconstructor = ReceiptTextReconstructor()

    def _formatted_text(key: ReceiptKey) -> str:
        ln_list = lines_by_key.get(key, [])
        text, groups = reconstructor.reconstruct_receipt(ln_list)
        # If focusing, log header anchors from top groups
        if focus_keys and key in focus_keys:
            header_groups = groups[:5]
            header_text = " \n".join(g.text for g in header_groups)
            header_phone = normalize_phone(header_text)
            header_addr = normalize_address(header_text)
            print(
                "[debug][header]",
                key,
                {"header_phone": header_phone, "header_address": header_addr},
            )
        return text

    # Chroma lines client (local snapshot)
    local_lines_path = str((ROOT / "dev.chroma_lines").resolve())
    try:
        chroma_line_client = VectorClient.create_line_client(
            persist_directory=local_lines_path, mode="read"
        )
        lines_collection = chroma_line_client.get_collection("lines")
    except Exception:
        lines_collection = None

    # OpenAI client for query-time embeddings to match batch dims (1536)
    try:
        _cm = None  # avoid dependency on client_manager config
        _openai = (
            OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            if os.environ.get("OPENAI_API_KEY")
            else None
        )
    except Exception:
        _openai = None

    # Google Places API client
    try:
        # Try Pulumi secrets first, then environment variable as fallback
        google_places_config = secrets.get("portfolio:GOOGLE_PLACES_API_KEY")
        google_places_key = (
            google_places_config.get("value") if google_places_config else None
        ) or os.environ.get("GOOGLE_PLACES_API_KEY")

        _places_api = (
            PlacesAPI(api_key=google_places_key) if google_places_key else None
        )
    except Exception:
        _places_api = None

    # Cost control for Google Places API
    _places_api_call_count = 0
    _places_api_max_calls = args.max_places_api_calls

    def _street_candidates_from_extracted(rk: ReceiptKey) -> List[str]:
        cands: List[str] = []
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "address":
                # Use the full line text for the street candidate (street-first heuristic)
                key = (str(w.image_id), int(w.receipt_id), int(w.line_id))
                street_line = line_text_by_key.get(key, "")
                if street_line:
                    cands.append(street_line)
        # Dedup, preserve order
        seen: Set[str] = set()
        out: List[str] = []
        for t in cands:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _phone_from_extracted(rk: ReceiptKey) -> str:
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                v = ext.get("value") or getattr(w, "text", "")
                ph = normalize_phone(str(v))
                if ph:
                    return ph
        return ""

    def _urls_by_receipt(
        words: List[ReceiptWord],
    ) -> Dict[ReceiptKey, Set[str]]:
        results: Dict[ReceiptKey, Set[str]] = defaultdict(set)
        for w in words:
            if not w.extracted_data or w.extracted_data.get("type") != "url":
                continue
            rk = (str(w.image_id), int(w.receipt_id))
            u = normalize_url(
                str(w.extracted_data.get("value") or w.text or "")
            )
            if u:
                results[rk].add(u)
        return results

    def _phone_line_candidates_from_extracted(rk: ReceiptKey) -> List[str]:
        cands: List[str] = []
        for w in words_by_receipt.get(rk, []):
            ext = getattr(w, "extracted_data", None) or {}
            if ext.get("type") == "phone":
                key = (str(w.image_id), int(w.receipt_id), int(w.line_id))
                phone_line = line_text_by_key.get(key, "")
                if phone_line:
                    cands.append(phone_line)
        seen: Set[str] = set()
        out: List[str] = []
        for t in cands:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _similarity_match_phone_first(rk: ReceiptKey) -> Dict:
        result: Dict = {"receipt": rk, "matches": []}
        if not lines_collection:
            result["error"] = "no_lines_collection"
            return result
        if _openai is None:
            result["error"] = "no_openai_client"
            return result
        phone_lines = _phone_line_candidates_from_extracted(rk)
        # limit to top few phone lines
        phone_lines = phone_lines[:2]
        if not phone_lines:
            result["error"] = "no_phone_on_receipt"
            return result
        for p in phone_lines:
            try:
                resp = _openai.embeddings.create(
                    model="text-embedding-3-small", input=[p]
                )
                emb = resp.data[0].embedding
                q = chroma_line_client.query(
                    collection_name="lines",
                    query_embeddings=[emb],
                    n_results=8,
                    include=["metadatas", "distances"],
                )
            except Exception as e:
                result.setdefault("errors", []).append(
                    {"phone_line": p, "error": str(e)}
                )
                continue
            mds = (q or {}).get("metadatas") or []
            dists = (q or {}).get("distances") or []
            phone_norm_line = normalize_phone(p)
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                try:
                    n_phone = str(
                        (md or {}).get("normalized_phone_10") or ""
                    ).strip()
                    n_addr = str(
                        (md or {}).get("normalized_full_address") or ""
                    ).strip()
                    n_img = str((md or {}).get("image_id") or "")
                    n_rec_str = (md or {}).get("receipt_id")
                    try:
                        n_rec = (
                            int(n_rec_str) if n_rec_str is not None else None
                        )
                    except Exception:
                        n_rec = None
                    phone_ok = bool(n_phone) and (phone_norm_line == n_phone)
                    why = {
                        "phone_line": p,
                        "phone_from_line": phone_norm_line,
                        "neighbor_phone": n_phone,
                        "neighbor_addr": n_addr,
                        "distance": dist,
                        "accepted": phone_ok,
                        "neighbor_receipt": [n_img, n_rec],
                    }
                    result["matches"].append(why)
                except Exception as e:
                    result.setdefault("errors", []).append(
                        {"md_error": str(e)}
                    )
        return result

    def _similarity_match_street_first(rk: ReceiptKey) -> Dict:
        result: Dict = {"receipt": rk, "matches": []}
        if not lines_collection:
            result["error"] = "no_lines_collection"
            return result
        street_lines = _street_candidates_from_extracted(rk)
        # limit to top few street candidates to control cost
        street_lines = street_lines[:3]
        phone_norm = _phone_from_extracted(rk)
        for s in street_lines:
            try:
                # Embed street text to match collection dims
                if _openai is None:
                    raise RuntimeError("no_openai_client")
                resp = _openai.embeddings.create(
                    model="text-embedding-3-small", input=[s]
                )
                emb = resp.data[0].embedding
                q = chroma_line_client.query(
                    collection_name="lines",
                    query_embeddings=[emb],
                    n_results=8,
                    include=["metadatas", "distances"],
                )
            except Exception as e:
                result.setdefault("errors", []).append(
                    {"street": s, "error": str(e)}
                )
                continue
            mds = (q or {}).get("metadatas") or []
            dists = (q or {}).get("distances") or []
            for md, dist in zip(
                mds[0] if mds else [], dists[0] if dists else []
            ):
                try:
                    n_addr = str(
                        (md or {}).get("normalized_full_address") or ""
                    ).strip()
                    n_phone_inline = str(
                        (md or {}).get("normalized_phone_10") or ""
                    ).strip()
                    street_norm = normalize_address(s)
                    addr_ok = bool(n_addr) and (
                        street_norm and street_norm.split()[0] in n_addr
                    )

                    # Chroma-side receipt-anchor fallback via local words index
                    neighbor_img = str((md or {}).get("image_id") or "")
                    neighbor_rec_str = (md or {}).get("receipt_id")
                    try:
                        neighbor_rec = (
                            int(neighbor_rec_str)
                            if neighbor_rec_str is not None
                            else None
                        )
                    except Exception:
                        neighbor_rec = None
                    fallback_phone = ""
                    if neighbor_img and neighbor_rec is not None:
                        for w in words_by_receipt.get(
                            (neighbor_img, neighbor_rec), []
                        ):
                            ext = getattr(w, "extracted_data", None) or {}
                            if ext.get("type") == "phone":
                                v = ext.get("value") or getattr(w, "text", "")
                                fp = normalize_phone(str(v))
                                if fp:
                                    fallback_phone = fp
                                    break

                    # Phone gating: only enforce equality when both sides have phones
                    if not phone_norm:
                        phone_ok = True
                    elif n_phone_inline:
                        phone_ok = phone_norm == n_phone_inline
                    elif fallback_phone:
                        phone_ok = phone_norm == fallback_phone
                    else:
                        phone_ok = (
                            True  # neighbor has no phone anchors; don't block
                        )

                    accepted = bool(addr_ok and phone_ok)
                    why = {
                        "street": s,
                        "street_norm": street_norm,
                        "neighbor_addr": n_addr,
                        "neighbor_phone_inline": n_phone_inline,
                        "neighbor_phone_fallback": fallback_phone,
                        "receipt_phone": phone_norm,
                        "addr_ok": addr_ok,
                        "phone_ok": phone_ok,
                        "distance": dist,
                        "accepted": accepted,
                        "neighbor_receipt": [neighbor_img, neighbor_rec],
                    }
                    result["matches"].append(why)
                except Exception as e:
                    result.setdefault("errors", []).append(
                        {"md_error": str(e)}
                    )
        return result

    def _google_places_fallback(rk: ReceiptKey) -> Dict:
        """Enhanced fallback to Google Places API when vector similarity doesn't find good matches.

        This function leverages extracted_data more effectively and creates ReceiptMetadata
        from successful Google Places API matches.

        Args:
            rk: Receipt key (image_id, receipt_id)

        Returns:
            Dict with Google Places API results, ReceiptMetadata, and error information
        """
        nonlocal _places_api_call_count

        result: Dict = {
            "receipt": rk,
            "places_results": [],
            "errors": [],
            "metadata": None,
        }

        if not _places_api:
            result["error"] = "no_places_api_client"
            return result

        # Check cost control limits
        if (
            _places_api_max_calls > 0
            and _places_api_call_count >= _places_api_max_calls
        ):
            result["error"] = "max_api_calls_exceeded"
            result["call_count"] = _places_api_call_count
            return result

        # Get address and phone from the receipt
        address = addr_by_receipt.get(rk, "")
        phones = phones_by_receipt.get(rk, set())

        if not address and not phones:
            result["error"] = "no_address_or_phone"
            return result

        # Get receipt words for business name detection
        receipt_words = words_by_receipt.get(rk, [])

        # Strategy 1: Try address search first (most reliable)
        if address:
            try:
                _places_api_call_count += 1
                places_result = _places_api.search_by_address(
                    address, receipt_words
                )

                if places_result:
                    places_info = {
                        "type": "address",
                        "query": address,
                        "place_id": places_result.get("place_id"),
                        "name": places_result.get("name"),
                        "formatted_address": places_result.get(
                            "formatted_address"
                        ),
                        "types": places_result.get("types", []),
                        "business_status": places_result.get(
                            "business_status"
                        ),
                        "confidence": "high",  # Address matches are generally reliable
                    }
                    result["places_results"].append(places_info)

                    # Create ReceiptMetadata from the successful match
                    result["metadata"] = (
                        _build_receipt_metadata_from_places_api(
                            places_result, rk, "address", address
                        )
                    )

            except Exception as e:
                result["errors"].append({"address_search": str(e)})

        # Strategy 2: Try phone search if no good address result
        if phones and not result["metadata"]:
            for phone in list(phones)[:2]:  # Limit to first 2 phone numbers
                try:
                    _places_api_call_count += 1
                    places_result = _places_api.search_by_phone(phone)

                    if places_result:
                        places_info = {
                            "type": "phone",
                            "query": phone,
                            "place_id": places_result.get("place_id"),
                            "name": places_result.get("name"),
                            "formatted_address": places_result.get(
                                "formatted_address"
                            ),
                            "types": places_result.get("types", []),
                            "business_status": places_result.get(
                                "business_status"
                            ),
                            "confidence": "medium",  # Phone matches are less reliable than address
                        }
                        result["places_results"].append(places_info)

                        # Create ReceiptMetadata from the successful match
                        result["metadata"] = (
                            _build_receipt_metadata_from_places_api(
                                places_result, rk, "phone", phone
                            )
                        )
                        break  # Use first successful phone match

                except Exception as e:
                    result["errors"].append({"phone_search": str(e)})

        # Strategy 3: Try business name + address combination using spatial context
        # Run if Strategy 1 and 2 failed, OR if Strategy 1 found only address (not business name)
        should_run_strategy_3 = (
            not result["metadata"] and not result["places_results"]
        ) or (
            result["places_results"]
            and any(
                place.get("name", "").upper() in address.upper()
                for place in result["places_results"]
            )
        )

        print(
            f"Strategy 3 decision: should_run={should_run_strategy_3}, has_metadata={bool(result['metadata'])}, has_places_results={bool(result['places_results'])}, has_receipt_words={bool(receipt_words)}"
        )

        if should_run_strategy_3 and receipt_words:
            business_name = None

            # Find business name using spatial context around the address
            business_name = _find_business_name_near_address(
                rk, receipt_words, lines
            )

            if business_name and address:
                try:
                    # Try searching with business name + address
                    combined_query = f"{business_name} {address}"
                    print(
                        f"Strategy 3 query: '{combined_query}' (length: {len(combined_query)})"
                    )
                    _places_api_call_count += 1
                    places_result = _places_api.search_by_address(
                        combined_query, receipt_words
                    )

                    if places_result:
                        places_info = {
                            "type": "business_address",
                            "query": combined_query,
                            "place_id": places_result.get("place_id"),
                            "name": places_result.get("name"),
                            "formatted_address": places_result.get(
                                "formatted_address"
                            ),
                            "types": places_result.get("types", []),
                            "business_status": places_result.get(
                                "business_status"
                            ),
                            "confidence": "high",  # Business name + address is very reliable
                            "merchant_in_text": True,  # We found the merchant name in the text
                        }
                        result["places_results"].append(places_info)

                        # Create ReceiptMetadata from the successful match
                        result["metadata"] = (
                            _build_receipt_metadata_from_places_api(
                                places_result,
                                rk,
                                "business_address",
                                combined_query,
                            )
                        )

                except Exception as e:
                    result["errors"].append(
                        {"business_address_search": str(e)}
                    )

        return result

    # Compute normalized anchors
    addr_by_receipt = _addresses_by_receipt(words, lines)
    phones_by_receipt = _phones_by_receipt(words)
    urls_by_receipt = _urls_by_receipt(words)

    # Build clusters keyed by address and url
    address_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, addr in addr_by_receipt.items():
        if addr and len(addr.split()) >= 3:
            address_clusters[addr].add(rk)

    url_clusters: Dict[str, Set[ReceiptKey]] = defaultdict(set)
    for rk, urls in urls_by_receipt.items():
        for u in urls:
            if u:
                url_clusters[u].add(rk)

    # Identify receipts missing metadata
    all_receipts: Set[ReceiptKey] = set(addr_by_receipt.keys())

    # Apply focus filter if provided
    focus_keys: Set[ReceiptKey] = set()
    for only in args.only:
        try:
            img, rec = only.split(":", 1)
            focus_keys.add((img, int(rec)))
        except Exception:
            print(
                f"[warn] Invalid --only value, expected IMAGE_ID:RECEIPT_ID, got: {only}"
            )
    if focus_keys:
        all_receipts = {rk for rk in all_receipts if rk in focus_keys}
        if not all_receipts:
            print("[]")
            return
    existing_map = _fetch_existing_metadata_map(client, all_receipts)

    # If focusing and include-existing, augment all_receipts to ensure we analyze even those with metadata
    if focus_keys and args.include_existing:
        all_receipts = focus_keys

    proposed: List[ReceiptMetadata] = []
    selection_log: List[Dict] = []

    if not args.similarity_report:
        # Address-based propagation
        for addr, rks in address_clusters.items():
            # Skip clusters that don't intersect focus when focus is set
            if focus_keys and not (rks & focus_keys):
                continue
            cluster_list = sorted(list(rks))
            have = [
                existing_map.get(rk)
                for rk in cluster_list
                if rk in existing_map
            ]
            have = [m for m in have if m is not None]
            # Determine missing set; if include-existing and focusing, treat focus receipts as "missing" for analysis output
            if focus_keys and args.include_existing:
                missing = [rk for rk in (rks & focus_keys)]
            else:
                missing = [rk for rk in cluster_list if rk not in existing_map]
            if not have or not missing:
                continue
            canonical = _choose_canonical_metadata(have)
            if not canonical:
                continue
            # Debug: log cluster decision
            if focus_keys:
                print("[debug][address-cluster] key=", addr)
                print("[debug] receipts=", cluster_list)
                print(
                    "[debug] have_merchants=",
                    [
                        (
                            m.image_id,
                            int(m.receipt_id),
                            m.merchant_name,
                            m.address,
                            m.phone_number,
                        )
                        for m in have
                    ],
                )
                print(
                    "[debug] picked_canonical=",
                    (
                        canonical.image_id,
                        int(canonical.receipt_id),
                        canonical.merchant_name,
                    ),
                )
            for rk in missing:
                # Header-derived anchors for comparison
                header_text = _formatted_text(rk)
                header_phone = normalize_phone(header_text)
                header_addr = normalize_address(header_text)

                # Basic city/zip token overlap diagnostics
                def _zip_of(addr_str: str) -> str:
                    import re as _re

                    m = _re.search(r"\b(\d{5})(?:-\d{4})?\b", addr_str)
                    return m.group(1) if m else ""

                def _city_of(addr_str: str) -> str:
                    # crude: take token before state if present
                    tokens = addr_str.split()
                    try:
                        idx = tokens.index("CA")
                        return " ".join(tokens[max(0, idx - 2) : idx])
                    except ValueError:
                        return ""

                cz_header = (_city_of(header_addr), _zip_of(header_addr))
                cz_canon = (
                    _city_of(
                        normalize_address(
                            canonical.address
                            or canonical.canonical_address
                            or ""
                        )
                    ),
                    _zip_of(
                        canonical.address or canonical.canonical_address or ""
                    ),
                )
                phones_match = bool(header_phone) and (
                    header_phone
                    == normalize_phone(
                        canonical.phone_number
                        or canonical.canonical_phone_number
                        or ""
                    )
                )
                print(
                    "[debug][compare]",
                    rk,
                    {
                        "header_phone": header_phone,
                        "canonical_phone": canonical.phone_number
                        or canonical.canonical_phone_number,
                        "phones_match": phones_match,
                        "header_address": header_addr,
                        "canonical_address": canonical.address
                        or canonical.canonical_address,
                        "city_zip_header": cz_header,
                        "city_zip_canonical": cz_canon,
                    },
                )
                reasons = ["address"]
                if phones_match:
                    reasons.append("phone")
                proposed.append(
                    _build_proposed_from_canonical(canonical, rk, reasons)
                )
            selection_log.append(
                {
                    "cluster_type": "address",
                    "key": addr,
                    "receipts": cluster_list,
                    "canonical_source": {
                        "image_id": canonical.image_id,
                        "receipt_id": int(canonical.receipt_id),
                        "merchant_name": canonical.merchant_name,
                    },
                    "proposed_count": len(missing),
                }
            )

        # URL-based propagation (only for those still missing)
        existing_after_addr = _fetch_existing_metadata_map(
            client, [rk for rk in all_receipts]
        )
        missing_now = [
            rk
            for rk in all_receipts
            if rk not in existing_after_addr
            and rk not in {(p.image_id, int(p.receipt_id)) for p in proposed}
        ]

        for url, rks in url_clusters.items():
            if focus_keys and not (rks & focus_keys):
                continue
            cluster_list = sorted(list(rks))
            have = [
                existing_after_addr.get(rk)
                for rk in cluster_list
                if rk in existing_after_addr
            ]
            have = [m for m in have if m is not None]
            if focus_keys and args.include_existing:
                missing = [rk for rk in (rks & focus_keys)]
            else:
                # exclude those already proposed
                missing = [rk for rk in cluster_list if rk in missing_now]
            if not have or not missing:
                continue
            canonical = _choose_canonical_metadata(have)
            if not canonical:
                continue
            if focus_keys:
                print("[debug][url-cluster] key=", url)
                print("[debug] receipts=", cluster_list)
                print(
                    "[debug] have_merchants=",
                    [
                        (
                            m.image_id,
                            int(m.receipt_id),
                            m.merchant_name,
                            m.address,
                            m.phone_number,
                        )
                        for m in have
                    ],
                )
                print(
                    "[debug] picked_canonical=",
                    (
                        canonical.image_id,
                        int(canonical.receipt_id),
                        canonical.merchant_name,
                    ),
                )
            for rk in missing:
                header_text = _formatted_text(rk)
                header_phone = normalize_phone(header_text)
                print(
                    "[debug][compare-url]",
                    rk,
                    {
                        "header_phone": header_phone,
                        "canonical_phone": canonical.phone_number
                        or canonical.canonical_phone_number,
                    },
                )
                proposed.append(
                    _build_proposed_from_canonical(canonical, rk, ["url"])
                )
            selection_log.append(
                {
                    "cluster_type": "url",
                    "key": url,
                    "receipts": cluster_list,
                    "canonical_source": {
                        "image_id": canonical.image_id,
                        "receipt_id": int(canonical.receipt_id),
                        "merchant_name": canonical.merchant_name,
                    },
                    "proposed_count": len(missing),
                }
            )

    # Deduplicate proposed by (image_id, receipt_id)
    deduped: Dict[ReceiptKey, ReceiptMetadata] = {}
    for p in proposed:
        key = (p.image_id, int(p.receipt_id))
        if key not in deduped:
            deduped[key] = p
        else:
            existing = deduped[key]
            # merge matched_fields and reasoning
            existing.matched_fields = sorted(
                list(
                    set(
                        (existing.matched_fields or [])
                        + (p.matched_fields or [])
                    )
                )
            )
            if p.reasoning and p.reasoning not in (existing.reasoning or ""):
                existing.reasoning = (
                    (existing.reasoning + "; " + p.reasoning)
                    if existing.reasoning
                    else p.reasoning
                )

    # Serialize entities to plain JSON objects
    def _serialize_entity(m: ReceiptMetadata) -> Dict:
        return {
            "image_id": m.image_id,
            "receipt_id": int(m.receipt_id),
            "place_id": m.place_id,
            "merchant_name": m.merchant_name,
            "merchant_category": m.merchant_category,
            "address": m.address,
            "phone_number": m.phone_number,
            "matched_fields": m.matched_fields,
            "validated_by": m.validated_by,
            "timestamp": m.timestamp.isoformat() if m.timestamp else "",
            "reasoning": m.reasoning,
            "canonical_place_id": m.canonical_place_id,
            "canonical_merchant_name": m.canonical_merchant_name,
            "canonical_address": m.canonical_address,
            "canonical_phone_number": m.canonical_phone_number,
            "validation_status": m.validation_status,
        }

    output: List[Dict] = []
    for key, m in deduped.items():
        output.append(
            {
                "receipt_metadata": _serialize_entity(m),
                "formatted_text": _formatted_text(key),
            }
        )

    # Apply focus filter to output
    if focus_keys:
        # If include-existing, also run similarity and attach diagnostics
        if args.include_existing:
            sim_logs = []
            phone_sim_logs = []
            places_logs = []
            for rk in focus_keys:
                sim_logs.append(_similarity_match_street_first(rk))
                phone_sim_logs.append(_similarity_match_phone_first(rk))
                if args.use_places_api:
                    places_logs.append(_google_places_fallback(rk))

            result_data = {
                "similarity": sim_logs,
                "phone_similarity": phone_sim_logs,
                "results": output,
            }

            if args.use_places_api:
                result_data["google_places"] = places_logs

            print(json.dumps(result_data, indent=2))
            return
        output = [
            o
            for o in output
            if (
                o["receipt_metadata"]["image_id"],
                int(o["receipt_metadata"]["receipt_id"]),
            )
            in focus_keys
        ]

    # Optional similarity report across receipts lacking metadata
    if args.similarity_report:
        # Build set of receipts missing metadata
        existing_all = _fetch_existing_metadata_map(
            client, addr_by_receipt.keys()
        )
        missing_receipts = [
            rk for rk in addr_by_receipt.keys() if rk not in existing_all
        ]
        matched = []
        unmatched = []
        places_api_matched = []
        places_api_metadata = (
            []
        )  # Store ReceiptMetadata from Google Places API
        reconstructor = ReceiptTextReconstructor()

        # helper to get full text for a receipt
        def _receipt_text(rk: ReceiptKey) -> str:
            ln_list = lines_by_key.get(rk, [])
            text, _ = reconstructor.reconstruct_receipt(ln_list)
            return text or ""

        for rk in missing_receipts:
            street = _similarity_match_street_first(rk)
            phone = _similarity_match_phone_first(rk)
            accepted_street = [
                m for m in street.get("matches", []) if m.get("accepted")
            ]
            accepted_phone = [
                m for m in phone.get("matches", []) if m.get("accepted")
            ]
            # Check if we have meaningful matches (not just empty merchant names)
            has_meaningful_match = False
            for m in accepted_street + accepted_phone:
                neighbor_key = tuple(m.get("neighbor_receipt") or ["", None])
                if (
                    neighbor_key
                    and neighbor_key[0]
                    and neighbor_key[1] is not None
                ):
                    nm = existing_all.get(
                        (neighbor_key[0], int(neighbor_key[1]))
                    )
                    if nm and (nm.canonical_merchant_name or nm.merchant_name):
                        has_meaningful_match = True
                        break

            if has_meaningful_match:
                reasons = []
                text = _receipt_text(rk).lower()
                # gather reasons and merchant checks
                for m in accepted_street:
                    neighbor_key = tuple(
                        m.get("neighbor_receipt") or ["", None]
                    )
                    merchant_name = ""
                    if (
                        neighbor_key
                        and neighbor_key[0]
                        and neighbor_key[1] is not None
                    ):
                        nm = existing_all.get(
                            (neighbor_key[0], int(neighbor_key[1]))
                        )
                        if nm:
                            merchant_name = (
                                nm.canonical_merchant_name
                                or nm.merchant_name
                                or ""
                            )
                    reasons.append(
                        {
                            "via": "street",
                            "street": m.get("street"),
                            "neighbor_receipt": m.get("neighbor_receipt"),
                            "addr_ok": m.get("addr_ok"),
                            "phone_ok": m.get("phone_ok"),
                            "distance": m.get("distance"),
                            "merchant_name": merchant_name,
                            "merchant_in_text": bool(
                                merchant_name and merchant_name.lower() in text
                            ),
                        }
                    )
                for m in accepted_phone:
                    neighbor_key = tuple(
                        m.get("neighbor_receipt") or ["", None]
                    )
                    merchant_name = ""
                    if (
                        neighbor_key
                        and neighbor_key[0]
                        and neighbor_key[1] is not None
                    ):
                        nm = existing_all.get(
                            (neighbor_key[0], int(neighbor_key[1]))
                        )
                        if nm:
                            merchant_name = (
                                nm.canonical_merchant_name
                                or nm.merchant_name
                                or ""
                            )
                    reasons.append(
                        {
                            "via": "phone",
                            "phone_line": m.get("phone_line"),
                            "neighbor_phone": m.get("neighbor_phone"),
                            "neighbor_receipt": m.get("neighbor_receipt"),
                            "distance": m.get("distance"),
                            "merchant_name": merchant_name,
                            "merchant_in_text": bool(
                                merchant_name and merchant_name.lower() in text
                            ),
                        }
                    )
                matched.append({"receipt": rk, "reasons": reasons})
            else:
                # Try Google Places API fallback if enabled
                if args.use_places_api:
                    places_result = _google_places_fallback(rk)
                    if places_result.get("places_results"):
                        text = _receipt_text(rk).lower()
                        places_reasons = []
                        for place in places_result["places_results"]:
                            merchant_name = place.get("name", "")
                            places_reasons.append(
                                {
                                    "via": "google_places",
                                    "type": place.get("type"),
                                    "query": place.get("query"),
                                    "place_id": place.get("place_id"),
                                    "name": merchant_name,
                                    "formatted_address": place.get(
                                        "formatted_address"
                                    ),
                                    "types": place.get("types", []),
                                    "business_status": place.get(
                                        "business_status"
                                    ),
                                    "confidence": place.get(
                                        "confidence", "unknown"
                                    ),
                                    "merchant_in_text": bool(
                                        merchant_name
                                        and merchant_name.lower() in text
                                    ),
                                }
                            )
                        places_api_matched.append(
                            {
                                "receipt": rk,
                                "reasons": places_reasons,
                                "places_api_errors": places_result.get(
                                    "errors", []
                                ),
                            }
                        )

                        # Collect ReceiptMetadata if available
                        if places_result.get("metadata"):
                            places_api_metadata.append(
                                places_result["metadata"]
                            )
                    else:
                        why = {
                            "street_errors": street.get("errors"),
                            "phone_errors": phone.get("errors"),
                            "street_candidates": len(
                                street.get("matches", [])
                            ),
                            "phone_candidates": len(phone.get("matches", [])),
                            "places_api_error": places_result.get("error"),
                            "places_api_errors": places_result.get(
                                "errors", []
                            ),
                        }
                        unmatched.append({"receipt": rk, "why": why})
                else:
                    why = {
                        "street_errors": street.get("errors"),
                        "phone_errors": phone.get("errors"),
                        "street_candidates": len(street.get("matches", [])),
                        "phone_candidates": len(phone.get("matches", [])),
                    }
                    unmatched.append({"receipt": rk, "why": why})
        report = {
            "total_missing": len(missing_receipts),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
            "matched": matched,
            "unmatched": unmatched,
        }

        # Add Google Places API results if enabled
        if args.use_places_api:
            report["places_api_matched_count"] = len(places_api_matched)
            report["places_api_matched"] = places_api_matched
            report["total_matched_with_places_api"] = len(matched) + len(
                places_api_matched
            )
        # ensure directory exists
        rp = Path(args.report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        with rp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(report, indent=2))
        summary = {
            "report_path": str(rp),
            "total_missing": report["total_missing"],
            "matched_count": report["matched_count"],
            "unmatched_count": report["unmatched_count"],
        }

        # Add Google Places API summary if enabled
        if args.use_places_api:
            summary["places_api_matched_count"] = report.get(
                "places_api_matched_count", 0
            )
            summary["total_matched_with_places_api"] = report.get(
                "total_matched_with_places_api", report["matched_count"]
            )

        print(json.dumps(summary))

        # Write Google Places API metadata to database if requested
        if args.write_places_api and places_api_metadata:
            try:
                client.add_receipt_metadatas(places_api_metadata)
                print(
                    f"Wrote {len(places_api_metadata)} ReceiptMetadata records from Google Places API to DynamoDB table {DDB_TABLE}."
                )
            except Exception as e:
                print(
                    f"Error writing Google Places API metadata to database: {e}"
                )
        elif places_api_metadata:
            print(
                f"Created {len(places_api_metadata)} ReceiptMetadata records from Google Places API (not written to database)."
            )

        # Report Google Places API usage and costs
        if args.use_places_api:
            estimated_cost = (
                _places_api_call_count * 0.017
            )  # Google Places API FindPlaceFromText: $0.017 per call
            print(f"\n=== GOOGLE PLACES API USAGE ===")
            print(f"Total API calls made: {_places_api_call_count}")
            print(f"Estimated cost: ${estimated_cost:.2f}")
            if _places_api_max_calls > 0:
                print(f"Call limit: {_places_api_max_calls}")
                print(
                    f"Remaining calls: {max(0, _places_api_max_calls - _places_api_call_count)}"
                )

        return

    print(json.dumps(output, indent=2))

    # Optional write (use deduped list)
    if args.write and deduped:
        to_write = list(deduped.values())
        if args.max_write and args.max_write > 0:
            to_write = to_write[: args.max_write]
        client.add_receipt_metadatas(to_write)
        print(
            f"Wrote {len(to_write)} ReceiptMetadata records to DynamoDB table {DDB_TABLE}."
        )


if __name__ == "__main__":
    main()
