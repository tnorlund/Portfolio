"""
Example showing how to migrate _receipt.py to use consolidated mixins.

This demonstrates the manual migration process before running the batch script.
"""

# ============================================
# BEFORE: Original with 6 ancestors
# ============================================

"""
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    CommonValidationMixin,
    DynamoDBBaseOperations,
    QueryByTypeMixin,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)

class _Receipt(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    QueryByTypeMixin,
    CommonValidationMixin,
):
    # ... implementation ...
"""

# ============================================
# AFTER: Migrated with 2 ancestors
# ============================================

from receipt_dynamo.data.base_operations import (
    FullDynamoEntityMixin,  # This contains all 5 mixins from above
)
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    handle_dynamodb_errors,
)


class _Receipt(
    DynamoDBBaseOperations,
    FullDynamoEntityMixin,
):
    """
    Receipt data access class using consolidated mixins.

    FullDynamoEntityMixin provides:
    - SingleEntityCRUDMixin: add_receipt, update_receipt, delete_receipt
    - BatchOperationsMixin: add_receipts with retry
    - TransactionalOperationsMixin: update_receipts transactionally
    - QueryByTypeMixin: list_receipts using GSITYPE
    - CommonValidationMixin: _validate_image_id, _validate_receipt_id

    All existing methods remain available and work identically.
    """

    # All your existing methods work exactly the same
    # No changes needed to the implementation

    @handle_dynamodb_errors("get_receipt")
    def get_receipt(self, image_id: str, receipt_id: int) -> Receipt:
        """Get a receipt - works exactly as before."""
        self._validate_image_id(image_id)  # From CommonValidationMixin
        self._validate_receipt_id(receipt_id)  # From CommonValidationMixin

        # Uses _get_entity from SingleEntityCRUDMixin through FullDynamoEntityMixin
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}",
            entity_class=Receipt,
            converter_func=item_to_receipt,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Receipt {receipt_id} for image {image_id} does not exist"
            )

        return result


# ============================================
# WHAT CHANGED?
# ============================================

"""
1. Import Changes:
   - Remove individual mixin imports
   - Add FullDynamoEntityMixin import

2. Class Definition:
   - Replace 5 mixins with 1 consolidated mixin
   - Keep DynamoDBBaseOperations as the first parent

3. Functionality:
   - NO CHANGES to any method implementations
   - All inherited methods still available
   - Same method signatures and behavior

4. Benefits:
   - Reduced from 6 ancestors to 2
   - Eliminates pylint warning
   - Clearer intent (this is a "full-featured" entity)
   - Easier to understand inheritance hierarchy
"""

# ============================================
# TESTING THE MIGRATION
# ============================================

"""
After migration, run these tests:

1. Unit tests for the specific file:
   pytest tests/unit/test__receipt.py -v

2. Integration tests:
   pytest tests/integration/test__receipt_integration.py -v

3. Verify no attribute errors:
   - Check all _method calls still work
   - Ensure validation methods are available
   - Test batch operations
   
4. Check pylint:
   pylint receipt_dynamo/data/_receipt.py
   # Should no longer show "too-many-ancestors" warning
"""
