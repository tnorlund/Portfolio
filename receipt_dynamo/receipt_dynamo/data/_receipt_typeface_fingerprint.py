"""DynamoDB operations for receipt typeface fingerprints."""

from datetime import datetime
from typing import cast

from receipt_dynamo.data.base_operations import (
    ConditionCheckTypeDef,
    FlattenedStandardMixin,
    TransactWriteItemTypeDef,
    UpdateTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities.receipt_typeface_fingerprint import (
    ReceiptTypefaceFingerprint,
    item_to_receipt_typeface_fingerprint,
)


class _ReceiptTypefaceFingerprint(FlattenedStandardMixin):
    """Single-record CRUD for deterministic receipt fingerprints."""

    @handle_dynamodb_errors("put_receipt_typeface_fingerprint")
    def put_receipt_typeface_fingerprint(
        self, fingerprint: ReceiptTypefaceFingerprint
    ) -> None:
        """Upsert evidence while retaining the independently owned Places name.

        Agreement is reset because it is derived from the evidence being
        replaced.  The upload adapter recomputes it from the retained name
        using an optimistic lock after this atomic evidence update.
        """

        self._validate_entity(
            fingerprint, ReceiptTypefaceFingerprint, "fingerprint"
        )
        item = fingerprint.to_item()
        evidence = {
            name: value
            for name, value in item.items()
            if name
            not in {"PK", "SK", "places_agreement", "places_merchant_name"}
        }
        attribute_names = {
            f"#{index}": name for index, name in enumerate(sorted(evidence))
        }
        expression_values = {
            f":{index}": evidence[name]
            for index, name in enumerate(sorted(evidence))
        }
        set_clauses = [
            f"{alias} = :{index}"
            for index, alias in enumerate(attribute_names)
        ]
        places_alias = "#places_agreement"
        attribute_names[places_alias] = "places_agreement"
        expression_values[":places_unavailable"] = {"S": "UNAVAILABLE"}
        set_clauses.append(f"{places_alias} = :places_unavailable")
        absent_optional = sorted(
            {"typeface", "abstention_reason"} - set(evidence)
        )
        remove_aliases = []
        for name in absent_optional:
            alias = f"#remove_{name}"
            attribute_names[alias] = name
            remove_aliases.append(alias)
        update_expression = "SET " + ", ".join(set_clauses)
        if remove_aliases:
            update_expression += " REMOVE " + ", ".join(remove_aliases)
        self._client.transact_write_items(
            TransactItems=[
                TransactWriteItemTypeDef(
                    ConditionCheck=ConditionCheckTypeDef(
                        TableName=self.table_name,
                        Key={
                            "PK": {"S": f"IMAGE#{fingerprint.image_id}"},
                            "SK": {
                                "S": f"RECEIPT#{fingerprint.receipt_id:05d}"
                            },
                        },
                        ConditionExpression="attribute_exists(PK)",
                    )
                ),
                TransactWriteItemTypeDef(
                    Update=UpdateTypeDef(
                        TableName=self.table_name,
                        Key=fingerprint.key,
                        UpdateExpression=update_expression,
                        ExpressionAttributeNames=attribute_names,
                        ExpressionAttributeValues=expression_values,
                    )
                ),
            ]
        )

    @handle_dynamodb_errors("delete_receipt_typeface_fingerprint")
    def delete_receipt_typeface_fingerprint(
        self, image_id: str, receipt_id: int
    ) -> None:
        """Idempotently delete exactly one additive fingerprint row."""

        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")
        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {"S": f"RECEIPT#{receipt_id:05d}#TYPEFACE_FINGERPRINT"},
            },
        )

    @handle_dynamodb_errors("get_receipt_typeface_fingerprint")
    def get_receipt_typeface_fingerprint(
        self, image_id: str, receipt_id: int
    ) -> ReceiptTypefaceFingerprint:
        """Return the fingerprint for one receipt."""

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=(f"RECEIPT#{receipt_id:05d}#TYPEFACE_FINGERPRINT"),
            entity_class=ReceiptTypefaceFingerprint,
            converter_func=item_to_receipt_typeface_fingerprint,
            consistent_read=True,
        )
        if result is None:
            raise EntityNotFoundError(
                f"No typeface fingerprint for {image_id}/{receipt_id}"
            )
        return cast(ReceiptTypefaceFingerprint, result)

    @handle_dynamodb_errors("update_receipt_typeface_places")
    def update_receipt_typeface_places(
        self,
        fingerprint: ReceiptTypefaceFingerprint,
        *,
        expected_places_merchant_name: str | None = None,
    ) -> None:
        """Conditionally update only the Places crosscheck fields.

        The calibration and creation identifiers provide optimistic locking:
        a concurrent re-OCR fingerprint cannot be overwritten by a stale
        merchant-resolution read.
        """

        self._validate_entity(
            fingerprint, ReceiptTypefaceFingerprint, "fingerprint"
        )
        assert isinstance(fingerprint.created_at, datetime)
        expression_values = {
            ":agreement": {"S": fingerprint.places_agreement},
            ":calibration_id": {"S": fingerprint.calibration_id},
            ":created_at": {"S": fingerprint.created_at.isoformat()},
        }
        condition_expression = (
            "attribute_exists(PK) AND calibration_id = :calibration_id "
            "AND created_at = :created_at"
        )
        if expected_places_merchant_name is None:
            condition_expression += (
                " AND attribute_not_exists(places_merchant_name)"
            )
        else:
            condition_expression += (
                " AND places_merchant_name = :expected_places_merchant_name"
            )
            expression_values[":expected_places_merchant_name"] = {
                "S": expected_places_merchant_name
            }
        update_expression = "SET places_agreement = :agreement"
        if fingerprint.places_merchant_name is None:
            update_expression += " REMOVE places_merchant_name"
        else:
            update_expression += ", places_merchant_name = :merchant_name"
            expression_values[":merchant_name"] = {
                "S": fingerprint.places_merchant_name
            }
        self._client.update_item(
            TableName=self.table_name,
            Key=fingerprint.key,
            UpdateExpression=update_expression,
            ConditionExpression=condition_expression,
            ExpressionAttributeValues=expression_values,
        )


__all__ = ["_ReceiptTypefaceFingerprint"]
