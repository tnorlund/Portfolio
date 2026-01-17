[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/formatLabel](../README.md) / formatLabel

# Function: formatLabel()

> **formatLabel**(`text`): `string`

Defined in: [utils/formatLabel.ts:10](https://github.com/tnorlund/Portfolio/blob/d68e03ead7818115f2d96c3e75e1f766f01745a8/portfolio/utils/formatLabel.ts#L10)

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
