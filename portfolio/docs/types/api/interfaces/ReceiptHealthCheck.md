[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthCheck

# Interface: ReceiptHealthCheck

Defined in: [types/api.ts:1091](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1091)

## Properties

### duration\_seconds

> **duration\_seconds**: `number` \| `null`

Defined in: [types/api.ts:1098](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1098)

***

### evidence\_count

> **evidence\_count**: `number`

Defined in: [types/api.ts:1113](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1113)

***

### id

> **id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1092](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1092)

***

### is\_llm

> **is\_llm**: `boolean`

Defined in: [types/api.ts:1097](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1097)

***

### question

> **question**: `string`

Defined in: [types/api.ts:1094](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1094)

***

### result

> **result**: `string`

Defined in: [types/api.ts:1112](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1112)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1095](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1095)

***

### summary

> **summary**: \{ `invalid`: `number`; `needs_review`: `number`; `total`: `number`; `valid`: `number`; \} \| \{ `has_invalid`: `boolean`; `has_needs_review`: `boolean`; `total_equations`: `number`; `mismatched_equations?`: `number`; \}

Defined in: [types/api.ts:1099](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1099)

***

### title

> **title**: `string`

Defined in: [types/api.ts:1093](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1093)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1096](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1096)

***

### what\_it\_validates

> **what\_it\_validates**: `string`[]

Defined in: [types/api.ts:1114](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1114)
