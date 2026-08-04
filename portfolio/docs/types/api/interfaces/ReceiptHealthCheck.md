[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthCheck

# Interface: ReceiptHealthCheck

Defined in: [types/api.ts:1020](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1020)

## Properties

### duration\_seconds

> **duration\_seconds**: `number` \| `null`

Defined in: [types/api.ts:1027](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1027)

***

### evidence\_count

> **evidence\_count**: `number`

Defined in: [types/api.ts:1042](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1042)

***

### id

> **id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1021](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1021)

***

### is\_llm

> **is\_llm**: `boolean`

Defined in: [types/api.ts:1026](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1026)

***

### question

> **question**: `string`

Defined in: [types/api.ts:1023](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1023)

***

### result

> **result**: `string`

Defined in: [types/api.ts:1041](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1041)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1024](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1024)

***

### summary

> **summary**: \{ `invalid`: `number`; `needs_review`: `number`; `total`: `number`; `valid`: `number`; \} \| \{ `has_invalid`: `boolean`; `has_needs_review`: `boolean`; `total_equations`: `number`; `mismatched_equations?`: `number`; \}

Defined in: [types/api.ts:1028](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1028)

***

### title

> **title**: `string`

Defined in: [types/api.ts:1022](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1022)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1025](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1025)

***

### what\_it\_validates

> **what\_it\_validates**: `string`[]

Defined in: [types/api.ts:1043](https://github.com/tnorlund/Portfolio/blob/09a6c08dd0822689b1814c01afcf081969edf74d/portfolio/types/api.ts#L1043)
