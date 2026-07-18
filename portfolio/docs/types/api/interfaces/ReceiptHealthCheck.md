[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthCheck

# Interface: ReceiptHealthCheck

Defined in: [types/api.ts:1019](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1019)

## Properties

### duration\_seconds

> **duration\_seconds**: `number` \| `null`

Defined in: [types/api.ts:1026](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1026)

***

### evidence\_count

> **evidence\_count**: `number`

Defined in: [types/api.ts:1041](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1041)

***

### id

> **id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1020](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1020)

***

### is\_llm

> **is\_llm**: `boolean`

Defined in: [types/api.ts:1025](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1025)

***

### question

> **question**: `string`

Defined in: [types/api.ts:1022](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1022)

***

### result

> **result**: `string`

Defined in: [types/api.ts:1040](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1040)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1023](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1023)

***

### summary

> **summary**: \{ `invalid`: `number`; `needs_review`: `number`; `total`: `number`; `valid`: `number`; \} \| \{ `has_invalid`: `boolean`; `has_needs_review`: `boolean`; `total_equations`: `number`; `mismatched_equations?`: `number`; \}

Defined in: [types/api.ts:1027](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1027)

***

### title

> **title**: `string`

Defined in: [types/api.ts:1021](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1021)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1024](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1024)

***

### what\_it\_validates

> **what\_it\_validates**: `string`[]

Defined in: [types/api.ts:1042](https://github.com/tnorlund/Portfolio/blob/ae5d6c940e554a6c2715db906c0ab13b39f97059/portfolio/types/api.ts#L1042)
