[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/formatLabel](../README.md) / formatLabel

# Function: formatLabel()

> **formatLabel**(`text`): `string`

Defined in: [utils/formatLabel.ts:10](https://github.com/tnorlund/Portfolio/blob/ddf9f2d749b2f83f188d31c47c24dd464d244297/portfolio/utils/formatLabel.ts#L10)

Format label/status for display: Title Case with special cases
- "id" becomes "ID"

## Parameters

### text

`string`

## Returns

`string`

## Example

```ts
formatLabel("LOYALTY_ID") // "Loyalty ID"
formatLabel("ADDRESS_LINE") // "Address Line"
formatLabel("NEEDS_REVIEW") // "Needs Review"
```
