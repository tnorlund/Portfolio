"""Identifier-aware alias resolution never bypasses a person's decision."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from receipt_dynamo.entities.merchant_truth import MerchantTruthActive
from receipt_dynamo.entities.product_alias import ProductAlias
from receipt_dynamo.merchant_truth_loader import build_fleet_alias_map

from receipt_nutrition.resolution import (
    AliasKeys,
    AliasRef,
    derive_alias_keys,
    identifier_key_text,
    load_fleet_alias_map,
    resolve_line,
    resolve_merchant_slug,
)

SLUG = "costco-wholesale"
AS_OF = date(2026, 9, 9)
CHANGED = datetime(2026, 9, 1, tzinfo=timezone.utc)
PRODUCT_A = ("costco:36946", "a" * 64)
PRODUCT_B = ("costco:99999", "b" * 64)
BUNDLE_HASH = "c" * 64


def alias(
    kind: str,
    text: str,
    *,
    status: str = "matched",
    method: str = "identifier",
    product: tuple[str, str] | None = PRODUCT_A,
    expires_in_days: int | None = 90,
    size: str | None = None,
    revision: int = 1,
) -> ProductAlias:
    user = method == "user"
    if status != "matched":
        product = None
    return ProductAlias(
        merchant_slug=SLUG,
        kind=kind,
        text=text,
        revision=revision,
        status=status,
        method=method,
        changed_at=CHANGED.isoformat(timespec="milliseconds"),
        applicability_json=json.dumps(
            {"merchant": SLUG, "text": text, "size": size}
        ),
        product_id=product[0] if product else None,
        product_revision=product[1] if product else None,
        confirmed_by_user=user,
        expires_at=(
            None
            if user
            else int((CHANGED + timedelta(days=expires_in_days)).timestamp())
        ),
    )


class FakeClient:
    """Read-only alias store; any other DynamoClient method is a failure."""

    def __init__(self, *rows: ProductAlias) -> None:
        self.rows = {(row.kind, row.text): row for row in rows}
        self.calls: list[tuple[str, str, str]] = []

    def get_product_alias(
        self, merchant_slug: str, kind: str, text: str
    ) -> ProductAlias | None:
        self.calls.append((merchant_slug, kind, text))
        return self.rows.get((kind, text))

    def __getattr__(self, name: str):
        raise AssertionError(f"resolution must not call {name}")


def resolve(client: FakeClient, line: str, size: str | None = None):
    return resolve_line(
        client,
        merchant_slug=SLUG,
        line_text=line,
        size_evidence=size,
        as_of=AS_OF,
    )


# --- alias keys ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "line", "expected"),
    [
        (
            SLUG,
            "E 36946 BEEF BULGOGI",
            AliasKeys("36946", "BEEF BULGOGI", "E 36946 BEEF BULGOGI"),
        ),
        (
            "costco",
            "36946 BEEF BULGOGI",
            AliasKeys("36946", "BEEF BULGOGI", "36946 BEEF BULGOGI"),
        ),
        (
            SLUG,
            "1055663 HNY RSTD MIX",
            AliasKeys("1055663", "HNY RSTD MIX", "1055663 HNY RSTD MIX"),
        ),
        (
            "target",
            "071-05-0012 GG BROCCOLI NF",
            AliasKeys(
                "071-05-0012", "GG BROCCOLI NF", "071 05 0012 GG BROCCOLI NF"
            ),
        ),
        (
            "target",
            "GG BROCCOLI 071-05-0012 NF",
            AliasKeys(
                "071-05-0012", "GG BROCCOLI NF", "GG BROCCOLI 071 05 0012 NF"
            ),
        ),
        (
            "vons",
            "000516221654 20 FAST SET",
            AliasKeys(
                "000516221654", "20 FAST SET", "000516221654 20 FAST SET"
            ),
        ),
        (SLUG, "E EGGS LARGE", AliasKeys(None, "EGGS LARGE", "E EGGS LARGE")),
        (SLUG, "Beef Bulgogi 2 LB", AliasKeys(None, "BEEF BULGOGI 2 LB")),
        (SLUG, "12 EGGS", AliasKeys(None, "12 EGGS")),
        (SLUG, "36946", AliasKeys("36946", "36946")),
        (SLUG, "E 36946", AliasKeys("36946", "36946", "E 36946")),
        (
            "sprouts",
            "36946 BEEF BULGOGI",
            AliasKeys(None, "36946 BEEF BULGOGI"),
        ),
        (
            "sprouts",
            "1000 ISLAND DRESSING",
            AliasKeys(None, "1000 ISLAND DRESSING"),
        ),
        (
            "costcoville",
            "1000 ISLAND DRESSING",
            AliasKeys(None, "1000 ISLAND DRESSING"),
        ),
    ],
)
def test_derive_alias_keys(slug: str, line: str, expected: AliasKeys) -> None:
    assert derive_alias_keys(line, merchant_slug=slug) == expected


def test_lookup_order_is_item_text_legacy() -> None:
    keys = derive_alias_keys("E 36946 BEEF BULGOGI", merchant_slug=SLUG)
    assert keys.lookups == [
        ("ITEM", "36946"),
        ("TEXT", "BEEF BULGOGI"),
        ("TEXT", "E 36946 BEEF BULGOGI"),
    ]
    assert derive_alias_keys("BEEF BULGOGI", merchant_slug=SLUG).lookups == [
        ("TEXT", "BEEF BULGOGI")
    ]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E 36946", "36946"),
        (" 36946 ", "36946"),
        ("0448", "0448"),
        ("071-05-0012", "071-05-0012"),
        ("000516221654", "000516221654"),
        ("123", None),
        ("36946 BEEF", None),
        ("", None),
        (None, None),
    ],
)
def test_identifier_key_text(raw: str | None, expected: str | None) -> None:
    assert identifier_key_text(raw) == expected


# --- merchant slug -----------------------------------------------------------


def test_slug_without_map_slugifies_raw() -> None:
    assert resolve_merchant_slug("COSTCO") == "costco"
    assert resolve_merchant_slug("Costco Wholesale") == "costco-wholesale"


def test_slug_with_map_uses_canonical_slug() -> None:
    fleet = {"costco": "costco-wholesale"}
    assert (
        resolve_merchant_slug("COSTCO", fleet_alias_map=fleet)
        == "costco-wholesale"
    )
    assert resolve_merchant_slug("Sprouts", fleet_alias_map=fleet) == "sprouts"


def test_slug_with_built_fleet_map() -> None:
    fleet = build_fleet_alias_map(
        [
            MerchantTruthActive(
                slug="costco-wholesale",
                version=1,
                bundle_hash=BUNDLE_HASH,
                normalized_aliases=["costco", "costco wholesale"],
                activated_at=CHANGED.isoformat(),
                activated_by="owner",
            )
        ]
    )
    assert (
        resolve_merchant_slug("Costco!", fleet_alias_map=fleet)
        == "costco-wholesale"
    )


@pytest.mark.parametrize("raw", [None, "", "   ", "###"])
def test_slug_blank_is_none(raw: str | None) -> None:
    assert resolve_merchant_slug(raw, fleet_alias_map={"": "x"}) is None


def test_load_fleet_alias_map_offline(tmp_path: Path) -> None:
    assert load_fleet_alias_map(tmp_path) is None
    active = MerchantTruthActive(
        slug="costco-wholesale",
        version=1,
        bundle_hash=BUNDLE_HASH,
        normalized_aliases=["costco"],
        activated_at=CHANGED.isoformat(),
        activated_by="owner",
    )
    (tmp_path / "fleet-active.json").write_text(
        json.dumps({"items": [active.to_item()]})
    )
    fleet = load_fleet_alias_map(tmp_path)
    assert fleet == {
        "costco": "costco-wholesale",
        "costco wholesale": "costco-wholesale",
    }
    assert (
        resolve_merchant_slug("COSTCO", fleet_alias_map=fleet)
        == "costco-wholesale"
    )


# --- resolution ---------------------------------------------------------------


def test_automatic_item_match_loses_to_user_rejected_text() -> None:
    client = FakeClient(
        alias("ITEM", "36946"),
        alias("TEXT", "BEEF BULGOGI", status="rejected", method="user"),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert result.status == "rejected"
    assert result.decided_by == "user"
    assert result.product_id is None and result.product_revision is None
    assert result.reason == "user_text"
    assert result.alias_refs == [
        AliasRef("ITEM", "36946", 1),
        AliasRef("TEXT", "BEEF BULGOGI", 1),
    ]


def test_user_rejection_under_legacy_full_text_key_still_decides() -> None:
    client = FakeClient(
        alias("ITEM", "36946"),
        alias(
            "TEXT", "E 36946 BEEF BULGOGI", status="rejected", method="user"
        ),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert (result.status, result.decided_by) == ("rejected", "user")
    assert result.reason == "user_legacy_text"
    assert result.alias_refs == [
        AliasRef("ITEM", "36946", 1),
        AliasRef("TEXT", "E 36946 BEEF BULGOGI", 1),
    ]


def test_bare_costco_identifier_reads_item_key() -> None:
    client = FakeClient(
        alias("ITEM", "36946", status="rejected", method="user"),
        alias("TEXT", "36946", method="lexical"),
    )
    result = resolve(client, "36946")
    assert (result.status, result.decided_by) == ("rejected", "user")
    assert client.calls == [(SLUG, "ITEM", "36946"), (SLUG, "TEXT", "36946")]


def test_costco_prefix_rule_is_costco_only() -> None:
    client = FakeClient()
    resolve_line(
        client,
        merchant_slug="sprouts",
        line_text="1000 ISLAND DRESSING",
        size_evidence=None,
        as_of=AS_OF,
    )
    assert client.calls == [("sprouts", "TEXT", "1000 ISLAND DRESSING")]


def test_user_confirmation_on_either_key_wins() -> None:
    by_item = FakeClient(
        alias("ITEM", "36946", method="user", product=PRODUCT_B),
        alias("TEXT", "BEEF BULGOGI", status="pending", method="lexical"),
    )
    result = resolve(by_item, "E 36946 BEEF BULGOGI")
    assert (result.status, result.decided_by) == ("matched", "user")
    assert (result.product_id, result.product_revision) == PRODUCT_B

    by_text = FakeClient(
        alias("ITEM", "36946", product=PRODUCT_A),
        alias("TEXT", "BEEF BULGOGI", method="user", product=PRODUCT_B),
    )
    result = resolve(by_text, "E 36946 BEEF BULGOGI")
    assert (result.status, result.decided_by) == ("matched", "user")
    assert (result.product_id, result.product_revision) == PRODUCT_B


def test_disagreeing_user_decisions_are_pending() -> None:
    client = FakeClient(
        alias("ITEM", "36946", method="user", product=PRODUCT_A, revision=3),
        alias("TEXT", "BEEF BULGOGI", method="user", product=PRODUCT_B),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert result.status == "pending"
    assert result.reason == "user_conflict"
    assert result.decided_by is None
    assert result.product_id is None
    assert result.alias_refs == [
        AliasRef("ITEM", "36946", 3),
        AliasRef("TEXT", "BEEF BULGOGI", 1),
    ]


def test_agreeing_user_decisions_resolve() -> None:
    client = FakeClient(
        alias("ITEM", "36946", method="user"),
        alias("TEXT", "BEEF BULGOGI", method="user"),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert (result.status, result.decided_by) == ("matched", "user")
    assert (result.product_id, result.product_revision) == PRODUCT_A


def test_expired_automatic_alias_is_pending_for_retry() -> None:
    client = FakeClient(alias("ITEM", "36946", expires_in_days=1))
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert result.status == "pending"
    assert result.reason == "expired_automatic"
    assert result.product_id is None
    assert result.alias_refs == [AliasRef("ITEM", "36946", 1)]


def test_expired_item_falls_through_to_live_text() -> None:
    client = FakeClient(
        alias("ITEM", "36946", expires_in_days=1, product=PRODUCT_B),
        alias("TEXT", "BEEF BULGOGI", method="lexical", product=PRODUCT_A),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert (result.status, result.decided_by) == ("matched", "automatic")
    assert result.reason == "automatic_text"
    assert (result.product_id, result.product_revision) == PRODUCT_A


def test_expiry_boundary_uses_as_of_instant() -> None:
    expires = CHANGED + timedelta(days=8)  # 2026-09-09T00:00:00+00:00
    client = FakeClient(alias("ITEM", "36946", expires_in_days=8))
    assert client.rows[("ITEM", "36946")].expires_at == int(
        expires.timestamp()
    )
    assert resolve(client, "36946 BEEF BULGOGI").status == "pending"
    live = resolve_line(
        client,
        merchant_slug=SLUG,
        line_text="36946 BEEF BULGOGI",
        size_evidence=None,
        as_of=expires - timedelta(seconds=1),
    )
    assert live.status == "matched"


def test_user_decisions_never_expire() -> None:
    client = FakeClient(alias("TEXT", "BEEF BULGOGI", method="user"))
    late = resolve_line(
        client,
        merchant_slug=SLUG,
        line_text="BEEF BULGOGI",
        size_evidence=None,
        as_of=date(2099, 1, 1),
    )
    assert (late.status, late.decided_by) == ("matched", "user")


def test_size_scoped_confirmation_does_not_resolve_other_size() -> None:
    client = FakeClient(
        alias("TEXT", "BEEF BULGOGI", method="user", size="16 oz")
    )
    other = resolve(client, "BEEF BULGOGI", size="32 oz")
    assert other.status == "unaliased"
    assert other.reason == "inapplicable_size"
    assert other.product_id is None
    assert other.alias_refs == [AliasRef("TEXT", "BEEF BULGOGI", 1)]

    same = resolve(client, "BEEF BULGOGI", size="16  OZ")
    assert (same.status, same.decided_by) == ("matched", "user")

    unknown = resolve(client, "BEEF BULGOGI", size=None)
    assert (unknown.status, unknown.decided_by) == ("matched", "user")


def test_oz_and_fl_oz_are_different_sizes() -> None:
    client = FakeClient(
        alias("TEXT", "BEEF BULGOGI", method="user", size="16 oz")
    )
    assert resolve(client, "BEEF BULGOGI", size="16 fl oz").status == (
        "unaliased"
    )


def test_inapplicable_user_scope_does_not_block_automatic_item() -> None:
    client = FakeClient(
        alias("ITEM", "36946", product=PRODUCT_A),
        alias(
            "TEXT",
            "BEEF BULGOGI",
            status="rejected",
            method="user",
            size="16 oz",
        ),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI", size="32 oz")
    assert (result.status, result.decided_by) == ("matched", "automatic")
    assert result.reason == "automatic_item"


def test_automatic_item_precedes_automatic_text() -> None:
    client = FakeClient(
        alias("ITEM", "36946", product=PRODUCT_A),
        alias("TEXT", "BEEF BULGOGI", method="lexical", product=PRODUCT_B),
    )
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert result.reason == "automatic_item"
    assert (result.product_id, result.product_revision) == PRODUCT_A


def test_automatic_negative_decisions_pass_through() -> None:
    client = FakeClient(
        alias("TEXT", "PAPER TOWELS", status="not_food", method="lexical")
    )
    result = resolve(client, "PAPER TOWELS")
    assert (result.status, result.decided_by) == ("not_food", "automatic")


def test_no_rows_is_unaliased_and_reads_only_derived_keys() -> None:
    client = FakeClient()
    result = resolve(client, "E 36946 BEEF BULGOGI")
    assert (result.status, result.reason) == ("unaliased", "no_alias")
    assert result.alias_refs == []
    assert client.calls == [
        (SLUG, "ITEM", "36946"),
        (SLUG, "TEXT", "BEEF BULGOGI"),
        (SLUG, "TEXT", "E 36946 BEEF BULGOGI"),
    ]

    client = FakeClient()
    resolve(client, "BEEF BULGOGI")
    assert client.calls == [(SLUG, "TEXT", "BEEF BULGOGI")]


def test_resolution_never_writes() -> None:
    client = FakeClient(
        alias("ITEM", "36946"),
        alias("TEXT", "BEEF BULGOGI", method="user"),
    )
    resolve(client, "E 36946 BEEF BULGOGI")
    with pytest.raises(AssertionError):
        client.save_product_alias  # pylint: disable=pointless-statement
    assert all(call[0] == SLUG for call in client.calls)
    assert len(client.calls) == 3
