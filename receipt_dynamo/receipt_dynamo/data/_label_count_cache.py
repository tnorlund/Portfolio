from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.label_count_cache import (
    LabelCountCache,
    item_to_label_count_cache,
)

if TYPE_CHECKING:
    pass


class _LabelCountCache(FlattenedStandardMixin):
    """Accessor methods for LabelCountCache items in DynamoDB."""

    @handle_dynamodb_errors("add_label_count_cache")
    def add_label_count_cache(self, item: LabelCountCache) -> None:
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise EntityValidationError(
                "item must be an instance of the LabelCountCache class."
            )
        self._add_entity(item)

    @handle_dynamodb_errors("add_label_count_caches")
    def add_label_count_caches(self, items: list[LabelCountCache]) -> None:
        if items is None:
            raise EntityValidationError("items cannot be None")
        if not isinstance(items, list) or not all(
            isinstance(item, LabelCountCache) for item in items
        ):
            raise EntityValidationError(
                "items must be a list of LabelCountCache objects.f"
            )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=item.to_item())
            )
            for item in items
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_label_count_cache")
    def update_label_count_cache(self, item: LabelCountCache) -> None:
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise EntityValidationError(
                "item must be an instance of the LabelCountCache class."
            )
        self._update_entity(item)

    @handle_dynamodb_errors("get_label_count_cache")
    def get_label_count_cache(self, label: str) -> Optional[LabelCountCache]:
        return self._get_entity(
            primary_key="LABEL_CACHE",
            sort_key=f"LABEL#{label}",
            entity_class=LabelCountCache,
            converter_func=item_to_label_count_cache,
        )

    @handle_dynamodb_errors("list_label_count_caches")
    def list_label_count_caches(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[LabelCountCache], Optional[Dict[str, Any]]]:
        return self._query_by_type(
            entity_type="LABEL_COUNT_CACHE",
            converter_func=item_to_label_count_cache,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
