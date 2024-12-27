from dynamo import ScaledImage, ItemToScaledImage
from decimal import Decimal, ROUND_HALF_UP
from botocore.exceptions import ClientError

def _format_float(
    value: float, decimal_places: int = 10, total_length: int = 20
) -> str:
    # Convert float → string → Decimal to avoid float binary representation issues
    d_value = Decimal(str(value))

    # Create a "quantizer" for the desired number of decimal digits
    # e.g. decimal_places=10 → quantizer = Decimal('1.0000000000')
    quantizer = Decimal("1." + "0" * decimal_places)

    # Round using the chosen rounding mode (e.g. HALF_UP)
    d_rounded = d_value.quantize(quantizer, rounding=ROUND_HALF_UP)

    # Format as a string with exactly `decimal_places` decimals
    formatted = f"{d_rounded:.{decimal_places}f}"

    # Optional: Pad to `total_length` characters
    # If you want leading zeros:
    if len(formatted) < total_length:
        formatted = formatted.zfill(total_length)

    # If instead you wanted trailing zeros, you could do:
    # formatted = formatted.ljust(total_length, '0')

    return formatted

class _ScaledImage:
    """
    A class used to represent a scaled image.
    """
    def addScaledImage(self, scaled_image: ScaledImage):
        """Adds a scaled image to the database

        Args:
            scaled_image (ScaledImage): The scaled image to add to the database

        Raises:
            ValueError: When a scaled image with the same ID already exists
        """
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=scaled_image.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise ValueError(f"Scaled image with ID {scaled_image.image_id} already exists")
        
    def getScaledImage(self, image_id: int, scale:float) -> ScaledImage:
        try:
            formatted_pk = f"IMAGE#{self.image_id:05d}"
            formatted_sk = (
                f"IMAGE_SCALE#{_format_float(self.scale, 4, 6).replace('.', '_')}"
            )
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": formatted_pk}, "SK": {"S": formatted_sk}},
            )
            return ItemToScaledImage(response["Item"])
        except KeyError:
            raise ValueError(f"Scaled image with ID {image_id} not found")
        
    def listScaledImages(self) -> list[ScaledImage]:
        response = self._client.scan(
            TableName=self.table_name,
            ScanFilter={
                "Type": {
                    "AttributeValueList": [{"S": "IMAGE_SCALE"}],
                    "ComparisonOperator": "EQ",
                }
            },
        )
        return [ItemToScaledImage(item) for item in response["Items"]]