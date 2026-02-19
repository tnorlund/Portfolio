[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/formatLabel](../README.md) / formatLabel

# Function: formatLabel()

> **formatLabel**(`text`): `string`

Defined in: [utils/formatLabel.ts:10](https://github.com/tnorlund/Portfolio/blob/e467bc6c5537dc9727e889c5c2ebda5692d65feb/portfolio/utils/formatLabel.ts#L10)

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
