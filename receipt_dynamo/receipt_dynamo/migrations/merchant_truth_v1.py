"""Read-only source collection and dry-run payloads for MerchantTruth v1."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map
from receipt_dynamo.entities.merchant_font import MerchantFont
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthAudit,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
    hash_payload,
)
from receipt_dynamo.merchant_truth_loader import normalize_merchant_alias

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client

DEV_TABLE_NAME = "ReceiptsTable-dc5be22"
ARTIFACT_BUCKET_ALIAS = "merchant-font-artifacts"
EXPECTED_MERCHANT_COUNT = 16
EXPECTED_MISSING_FONT_SLUGS = frozenset(
    {"amazon_fresh", "smith_s", "dollar_tree"}
)

MEASURED_TYPOGRAPHY_FIELDS = frozenset(
    {
        "bitmap_font",
        "bitmap_thin",
        "condense",
        "ocr_font_sizing",
        "bitmap_cap_ratio",
        "ocr_cap_height_ratio",
        "pitch_ratio",
        "ink",
        "bitmap_glyph_vscale",
    }
)
DECIDED_TYPOGRAPHY_FIELDS = frozenset(
    {
        "font",
        "stroke",
        "display_headings",
        "reverse_date_after_items",
        "reverse_date_anchor",
        "dash_after_amount_date",
        "dash_around_phrases",
        "dashed_separators",
        "face_source",
        "heading_bleed_phrase",
        "reverse_total",
        "mixed_layout",
        "condense_glyphs",
    }
)


class UnmappedMerchantTruthLeafError(ValueError):
    """Raised when a source profile leaf has no normative disposition."""


def slugify_merchant(name: str) -> str:
    """Match ``MerchantCatalogItem.slugify_merchant`` exactly."""
    return "".join(
        character if character.isalnum() else "_"
        for character in name.strip().lower()
    ).strip("_")


@dataclass(frozen=True)
class LeafDisposition:
    """Machine-readable source-to-component crosswalk entry."""

    source_path: str
    classification: str
    destination: str | None
    reason: str | None = None


@dataclass(frozen=True)
class MerchantV1Payload:
    """Exact low-level mint items plus owner-visible blockers."""

    merchant_name: str
    slug: str
    items: list[dict[str, Any]]
    blockers: list[str]

    def to_document(self) -> dict[str, Any]:
        return {
            "merchant_name": self.merchant_name,
            "slug": self.slug,
            "version": 1,
            "dry_run": True,
            "migration_blockers": self.blockers,
            "items": self.items,
        }


class ReadOnlyMerchantTruthSource:
    """AWS source adapter deliberately exposing no mutation methods."""

    def __init__(
        self,
        dynamodb_client: "DynamoDBClient",
        s3_client: "S3Client",
        table_name: str,
    ) -> None:
        if table_name != DEV_TABLE_NAME:
            raise ValueError(
                f"dry-run migration only reads exact dev table "
                f"{DEV_TABLE_NAME!r}; got {table_name!r}"
            )
        self._dynamodb = dynamodb_client
        self._s3 = s3_client
        self._table_name = table_name

    def list_merchant_font_items(self) -> list[dict[str, Any]]:
        """Read every current MerchantFont pointer through GSITYPE."""
        return self._query_all(
            TableName=self._table_name,
            IndexName="GSITYPE",
            KeyConditionExpression="#type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={":type": {"S": "MERCHANT_FONT"}},
        )

    def list_catalog_items(self, slug: str) -> list[dict[str, Any]]:
        """Strongly read the current catalog authoring partition."""
        return self._query_all(
            TableName=self._table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": f"MERCHANT_CATALOG#{slug}"},
                ":sk": {"S": "ITEM#"},
            },
            ConsistentRead=True,
        )

    def read_object(self, bucket: str, key: str) -> bytes:
        """Fetch object bytes so migration computes hashes independently."""
        response = self._s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def _query_all(self, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            request = dict(params)
            if exclusive_start_key is not None:
                request["ExclusiveStartKey"] = exclusive_start_key
            response = self._dynamodb.query(**request)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return items


def iter_leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> Iterable[str]:
    """Yield stable leaf paths, using ``[]`` for any list element."""
    if isinstance(value, dict):
        for key in sorted(value):
            yield from iter_leaf_paths(value[key], (*prefix, key))
        return
    if isinstance(value, list):
        if not value:
            yield ".".join((*prefix, "[]"))
            return
        for item in value:
            yield from iter_leaf_paths(item, (*prefix, "[]"))
        return
    yield ".".join(prefix)


def classify_leaf(  # pylint: disable=too-many-return-statements
    path: str,
) -> LeafDisposition:
    """Classify one concrete profile-document leaf or fail closed."""
    if path in {"_comment", "_section_scale_note"}:
        return LeafDisposition(
            path,
            "discarded-with-reason",
            None,
            "registry documentation only; recorded in crosswalk report",
        )
    parts = path.split(".")
    if len(parts) < 3 or parts[0] != "profiles":
        raise UnmappedMerchantTruthLeafError(f"unmapped source leaf: {path}")
    relative = parts[2:]
    root = relative[0]
    if root == "_comment":
        return LeafDisposition(
            path,
            "discarded-with-reason",
            None,
            "human migration note; not a runtime input",
        )
    if root == "aliases":
        return LeafDisposition(path, "identity", "C#identity.aliases")
    if root == "layout_template":
        if len(relative) > 1 and relative[1] == "_comment":
            return LeafDisposition(
                path,
                "discarded-with-reason",
                None,
                "layout migration note; provenance is explicit",
            )
        return LeafDisposition(path, "measured", "C#layout")
    if root == "section_scale":
        return LeafDisposition(path, "measured", "C#typography.section_scale")
    if root == "typography":
        if len(relative) < 2:
            raise UnmappedMerchantTruthLeafError(
                f"unmapped typography leaf: {path}"
            )
        field_name = relative[1]
        if field_name == "_face_source_comment":
            return LeafDisposition(
                path,
                "discarded-with-reason",
                None,
                "legacy explanatory comment",
            )
        if field_name == "stylemap":
            return LeafDisposition(
                path, "asset", "C#assets.profile_stylemap_filename"
            )
        if field_name in MEASURED_TYPOGRAPHY_FIELDS:
            return LeafDisposition(
                path, "measured", f"C#typography.{field_name}"
            )
        if field_name in DECIDED_TYPOGRAPHY_FIELDS:
            return LeafDisposition(
                path,
                "decided",
                f"C#flags.typography.{field_name}",
            )
        raise UnmappedMerchantTruthLeafError(
            f"unmapped typography leaf: {path}"
        )
    if root in {"header", "graphics", "compose"}:
        return LeafDisposition(path, "decided", f"C#flags.{root}")
    if root in {"logo_subtitle", "logo_reserve_subtitle"}:
        return LeafDisposition(path, "decided", f"C#flags.{root}")
    if root in {"logo", "logo_anchor"}:
        return LeafDisposition(path, "asset", f"C#assets.profile.{root}")
    raise UnmappedMerchantTruthLeafError(f"unmapped source leaf: {path}")


def build_crosswalk(document: dict[str, Any]) -> list[LeafDisposition]:
    """Classify every distinct leaf and enforce the 16-merchant source."""
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("merchant profile document must contain profiles map")
    if len(profiles) != EXPECTED_MERCHANT_COUNT:
        raise ValueError(
            f"expected {EXPECTED_MERCHANT_COUNT} profiles, got {len(profiles)}"
        )
    paths = sorted(set(iter_leaf_paths(document)))
    return [classify_leaf(path) for path in paths]


def _select_keys(
    source: dict[str, Any], names: frozenset[str]
) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in source.items()
        if key in names
    }


def _migration_provenance(
    source_path: str,
    git_sha: str,
    generated_at: str,
    source_objects: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "source_kind": "migration",
        "written_by": {
            "kind": "migration",
            "name": "merchant_truth_v1",
            "version": "1",
        },
        "source_path": source_path,
        "source_objects": source_objects or [],
        "git_sha": git_sha,
        "pipeline": "merchant_truth_v1",
        "pipeline_version": "1",
        "measured_at": None,
        "source_receipt_keys": [],
        "provenance_completeness": "legacy",
        "migrated_at": generated_at,
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _catalog_record(item: dict[str, Any]) -> dict[str, Any]:
    data = parse_dynamodb_map(item)
    return {
        key: value
        for key, value in data.items()
        if key not in {"PK", "SK", "TYPE", "GSI1PK", "GSI1SK"}
    }


def _font_payload(font: MerchantFont) -> dict[str, Any]:
    return {
        "bucket_alias": ARTIFACT_BUCKET_ALIAS,
        "s3_key": font.s3_key,
        "content_hash": font.content_hash,
        "source_commit": font.source_commit,
        "compiled_at": font.compiled_at,
        "cap_h": font.cap_h,
        "advance_ratio": font.advance_ratio,
        "pitch_check": font.pitch_check,
        "glyph_count": font.glyph_count,
        "cache_filename": font.cache_filename,
    }


def _find_local_stylemap(
    merchant_name: str, stylemap_root: Path | None
) -> Path | None:
    if stylemap_root is None or not stylemap_root.exists():
        return None
    merchant_token = "".join(
        character
        for character in merchant_name.casefold()
        if character.isalnum()
    )
    for path in stylemap_root.rglob("stylemap.json"):
        folder_token = "".join(
            character
            for character in path.parent.name.casefold()
            if character.isalnum()
        )
        if folder_token == merchant_token:
            return path
    return None


def _artifact_document(
    *,
    key: str,
    content: bytes,
) -> dict[str, Any]:
    return {
        "bucket_alias": ARTIFACT_BUCKET_ALIAS,
        "s3_key": key,
        "content_hash": _sha256_bytes(content),
        "size": len(content),
    }


def build_v1_payloads(
    document: dict[str, Any],
    source: ReadOnlyMerchantTruthSource,
    *,
    profiles_source_path: str,
    git_sha: str,
    generated_at: str,
    stylemap_root: Path | None = None,
) -> tuple[list[MerchantV1Payload], list[LeafDisposition]]:
    """Build all exact nine-item mint payloads without any DynamoDB writes."""
    crosswalk = build_crosswalk(document)
    profiles: dict[str, dict[str, Any]] = document["profiles"]
    font_rows = [
        MerchantFont.from_item(item)
        for item in source.list_merchant_font_items()
    ]
    fonts_by_name: dict[str, list[MerchantFont]] = {}
    for font in font_rows:
        fonts_by_name.setdefault(font.merchant_name, []).append(font)

    missing_slugs = {
        slugify_merchant(name)
        for name in profiles
        if name not in fonts_by_name
    }
    if missing_slugs != EXPECTED_MISSING_FONT_SLUGS:
        raise ValueError(
            "MerchantFont coverage changed; expected missing "
            f"{sorted(EXPECTED_MISSING_FONT_SLUGS)}, got "
            f"{sorted(missing_slugs)}"
        )

    payloads = [
        _build_merchant_payload(
            merchant_name,
            profile,
            fonts_by_name.get(merchant_name, []),
            source,
            profiles_source_path=profiles_source_path,
            git_sha=git_sha,
            generated_at=generated_at,
            stylemap_root=stylemap_root,
        )
        for merchant_name, profile in sorted(profiles.items())
    ]
    return payloads, crosswalk


def _build_merchant_payload(
    merchant_name: str,
    profile: dict[str, Any],
    fonts: list[MerchantFont],
    source: ReadOnlyMerchantTruthSource,
    *,
    profiles_source_path: str,
    git_sha: str,
    generated_at: str,
    stylemap_root: Path | None,
) -> MerchantV1Payload:
    slug = slugify_merchant(merchant_name)
    blockers: list[str] = []
    aliases = [merchant_name, *profile.get("aliases", [])]
    normalized_aliases = sorted(
        {normalize_merchant_alias(alias) for alias in [slug, *aliases]}
    )
    identity = {
        "merchant_name": merchant_name,
        "slug": slug,
        "aliases": profile.get("aliases", []),
        "upper_slug": slug.upper(),
        "normalized_aliases": normalized_aliases,
    }
    typography_source = profile.get("typography", {})
    typography = {
        "typography": _select_keys(
            typography_source, MEASURED_TYPOGRAPHY_FIELDS
        ),
        "section_scale": copy.deepcopy(profile.get("section_scale", {})),
    }
    flags: dict[str, Any] = {
        "typography": _select_keys(
            typography_source, DECIDED_TYPOGRAPHY_FIELDS
        )
    }
    for key in (
        "header",
        "graphics",
        "compose",
        "logo_subtitle",
        "logo_reserve_subtitle",
    ):
        if key in profile:
            flags[key] = copy.deepcopy(profile[key])
    layout_template = copy.deepcopy(profile.get("layout_template"))
    if isinstance(layout_template, dict):
        layout_template.pop("_comment", None)
    layout = {
        "available": layout_template is not None,
        "template": layout_template,
    }

    assets, stylemap, asset_sources = _build_assets_and_stylemap(
        merchant_name,
        profile,
        fonts,
        source,
        blockers,
        stylemap_root,
    )
    catalog_records = sorted(
        (_catalog_record(item) for item in source.list_catalog_items(slug)),
        key=lambda item: (
            str(item.get("category", "")),
            str(item.get("product_text", "")),
            str(item.get("price", "")),
        ),
    )
    catalog_snapshot = {
        "items": catalog_records,
        "item_count": len(catalog_records),
        "catalog_hash": hash_payload(catalog_records),
        "as_of": generated_at,
    }
    payload_by_name = {
        "identity": identity,
        "typography": typography,
        "stylemap": stylemap,
        "layout": layout,
        "assets": assets,
        "flags": flags,
        "catalog_snapshot": catalog_snapshot,
    }
    if set(payload_by_name) != COMPONENT_NAMES:
        raise AssertionError("migration component set drifted")

    components = []
    for name in sorted(payload_by_name):
        source_objects = (
            asset_sources if name in {"assets", "stylemap"} else []
        )
        components.append(
            MerchantTruthComponent(
                slug=slug,
                version=1,
                name=name,
                payload=payload_by_name[name],
                provenance=_migration_provenance(
                    profiles_source_path,
                    git_sha,
                    generated_at,
                    source_objects,
                ),
            )
        )
    component_hashes = {item.name: item.content_hash for item in components}
    bundle_hash = compute_bundle_hash(component_hashes)
    run_id = f"merchant-truth-v1-{slug}-{git_sha[:12]}"
    manifest = MerchantTruthManifest(
        slug=slug,
        version=1,
        component_hashes=component_hashes,
        bundle_hash=bundle_hash,
        status="OPEN",
        provenance={
            "written_by": {
                "kind": "migration",
                "name": "merchant_truth_v1",
                "version": "1",
            },
            "minted_at": generated_at,
            "run_id": run_id,
            "git_sha": git_sha,
            "migration_blockers": blockers,
        },
        mint_run_id=run_id,
    )
    audit_seed = f"{slug}:{bundle_hash}:{generated_at}:MINT"
    audit = MerchantTruthAudit(
        slug=slug,
        created_at=generated_at,
        audit_id=hashlib.sha256(audit_seed.encode("utf-8")).hexdigest()[:26],
        action="MINT",
        version=1,
        bundle_hash=bundle_hash,
        details={
            "run_id": run_id,
            "component_count": len(components),
            "dry_run": True,
            "migration_blockers": blockers,
        },
    )
    return MerchantV1Payload(
        merchant_name=merchant_name,
        slug=slug,
        items=[
            manifest.to_item(),
            *[item.to_item() for item in components],
            audit.to_item(),
        ],
        blockers=blockers,
    )


def _build_assets_and_stylemap(
    merchant_name: str,
    profile: dict[str, Any],
    fonts: list[MerchantFont],
    source: ReadOnlyMerchantTruthSource,
    blockers: list[str],
    stylemap_root: Path | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    asset_sources: list[str] = []
    fonts_payload = {font.face: _font_payload(font) for font in fonts}
    profile_assets = {
        key: copy.deepcopy(profile[key])
        for key in ("logo", "logo_anchor")
        if key in profile
    }
    if "stylemap" in profile.get("typography", {}):
        profile_assets["stylemap_filename"] = profile["typography"]["stylemap"]
    assets: dict[str, Any] = {
        "fonts": fonts_payload,
        "profile": profile_assets,
        "logo": None,
        "missing_merchant_font": not fonts,
    }
    if not fonts:
        blockers.append(
            "missing MerchantFont row; publish/import assets before seal"
        )

    regular = next((font for font in fonts if font.face == "regular"), None)
    stylemap: dict[str, Any] = {
        "available": False,
        "document": None,
        "source": None,
    }
    if regular and regular.stylemap_s3_key:
        content = source.read_object(
            regular.s3_bucket, regular.stylemap_s3_key
        )
        artifact = _artifact_document(
            key=regular.stylemap_s3_key,
            content=content,
        )
        stylemap = {
            "available": True,
            "document": json.loads(content.decode("utf-8")),
            "source": artifact,
        }
        asset_sources.append(
            f"s3://{regular.s3_bucket}/{regular.stylemap_s3_key}"
        )
    else:
        local_stylemap = _find_local_stylemap(merchant_name, stylemap_root)
        if local_stylemap is not None:
            content = local_stylemap.read_bytes()
            stylemap = {
                "available": True,
                "document": json.loads(content.decode("utf-8")),
                "source": {
                    "local_path": str(local_stylemap),
                    "content_hash": _sha256_bytes(content),
                    "size": len(content),
                    "published": False,
                },
            }
            asset_sources.append(str(local_stylemap))
            blockers.append(
                "local stylemap is not published to MerchantFont/S3"
            )

    if regular and regular.logo_s3_key:
        logo_content = source.read_object(
            regular.s3_bucket, regular.logo_s3_key
        )
        assets["logo"] = _artifact_document(
            key=regular.logo_s3_key,
            content=logo_content,
        )
        asset_sources.append(f"s3://{regular.s3_bucket}/{regular.logo_s3_key}")
    elif profile.get("logo"):
        blockers.append(
            "profile logo has no published MerchantFont S3 pointer"
        )
    return assets, stylemap, asset_sources


def write_dry_run_payloads(
    output_dir: Path,
    payloads: list[MerchantV1Payload],
    crosswalk: list[LeafDisposition],
    *,
    generated_at: str,
    git_sha: str,
) -> None:
    """Write deterministic owner-inspection documents, never DynamoDB."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for payload in payloads:
        _write_json(output_dir / f"{payload.slug}.json", payload.to_document())
    _write_json(
        output_dir / "_crosswalk.json",
        {
            "entries": [
                {
                    "source_path": item.source_path,
                    "classification": item.classification,
                    "destination": item.destination,
                    "reason": item.reason,
                }
                for item in crosswalk
            ]
        },
    )
    _write_json(
        output_dir / "_summary.json",
        {
            "dry_run": True,
            "generated_at": generated_at,
            "git_sha": git_sha,
            "merchant_count": len(payloads),
            "missing_merchant_font_slugs": sorted(
                payload.slug
                for payload in payloads
                if any(
                    "missing MerchantFont" in blocker
                    for blocker in payload.blockers
                )
            ),
            "blocked_merchants": {
                payload.slug: payload.blockers
                for payload in payloads
                if payload.blockers
            },
        },
    )


def _write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
