[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthCheck

# Interface: ReceiptHealthCheck

Defined in: [types/api.ts:911](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L911)

## Properties

### duration\_seconds

> **duration\_seconds**: `number` \| `null`

Defined in: [types/api.ts:918](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L918)

***

### evidence\_count

> **evidence\_count**: `number`

Defined in: [types/api.ts:933](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L933)

***

### id

> **id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:912](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L912)

***

### is\_llm

> **is\_llm**: `boolean`

Defined in: [types/api.ts:917](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L917)

***

### question

> **question**: `string`

Defined in: [types/api.ts:914](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L914)

***

### result

> **result**: `string`

Defined in: [types/api.ts:932](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L932)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:915](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L915)

***

### summary

> **summary**: \{ `invalid`: `number`; `needs_review`: `number`; `total`: `number`; `valid`: `number`; \} \| \{ `has_invalid`: `boolean`; `has_needs_review`: `boolean`; `total_equations`: `number`; `mismatched_equations?`: `number`; \}

Defined in: [types/api.ts:919](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L919)

***

### title

> **title**: `string`

Defined in: [types/api.ts:913](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L913)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:916](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L916)

***

### what\_it\_validates

> **what\_it\_validates**: `string`[]

Defined in: [types/api.ts:934](https://github.com/tnorlund/Portfolio/blob/139aa0a7839bce94c14632a1e748feee67ea7f3b/portfolio/types/api.ts#L934)
