[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthCheck

# Interface: ReceiptHealthCheck

Defined in: [types/api.ts:988](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L988)

## Properties

### duration\_seconds

> **duration\_seconds**: `number` \| `null`

Defined in: [types/api.ts:995](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L995)

***

### evidence\_count

> **evidence\_count**: `number`

Defined in: [types/api.ts:1010](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L1010)

***

### id

> **id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:989](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L989)

***

### is\_llm

> **is\_llm**: `boolean`

Defined in: [types/api.ts:994](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L994)

***

### question

> **question**: `string`

Defined in: [types/api.ts:991](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L991)

***

### result

> **result**: `string`

Defined in: [types/api.ts:1009](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L1009)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:992](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L992)

***

### summary

> **summary**: \{ `invalid`: `number`; `needs_review`: `number`; `total`: `number`; `valid`: `number`; \} \| \{ `has_invalid`: `boolean`; `has_needs_review`: `boolean`; `total_equations`: `number`; `mismatched_equations?`: `number`; \}

Defined in: [types/api.ts:996](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L996)

***

### title

> **title**: `string`

Defined in: [types/api.ts:990](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L990)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:993](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L993)

***

### what\_it\_validates

> **what\_it\_validates**: `string`[]

Defined in: [types/api.ts:1011](https://github.com/tnorlund/Portfolio/blob/e7d85ffe885b75b10e6285dbc8d1cea8391d51d9/portfolio/types/api.ts#L1011)
