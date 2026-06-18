[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthLedgerIssue

# Interface: ReceiptHealthLedgerIssue

Defined in: [types/api.ts:1142](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1142)

## Properties

### check\_id

> **check\_id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1151](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1151)

***

### check\_title

> **check\_title**: `string`

Defined in: [types/api.ts:1152](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1152)

***

### evidence

> **evidence**: `Record`\<`string`, `unknown`\>[]

Defined in: [types/api.ts:1158](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1158)

***

### execution\_id

> **execution\_id**: `string`

Defined in: [types/api.ts:1145](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1145)

***

### fingerprint

> **fingerprint**: `string`

Defined in: [types/api.ts:1144](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1144)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:1147](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1147)

***

### issue\_id

> **issue\_id**: `string`

Defined in: [types/api.ts:1143](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1143)

***

### issue\_type

> **issue\_type**: `string`

Defined in: [types/api.ts:1155](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1155)

***

### message

> **message**: `string`

Defined in: [types/api.ts:1156](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1156)

***

### observed\_at

> **observed\_at**: `string`

Defined in: [types/api.ts:1146](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1146)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:1148](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1148)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1154](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1154)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1153](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1153)

***

### attempt\_count?

> `optional` **attempt\_count**: `number`

Defined in: [types/api.ts:1166](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1166)

***

### first\_seen\_at?

> `optional` **first\_seen\_at**: `string`

Defined in: [types/api.ts:1161](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1161)

***

### first\_seen\_execution\_id?

> `optional` **first\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1162](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1162)

***

### known\_limitation\_reason?

> `optional` **known\_limitation\_reason**: `string`

Defined in: [types/api.ts:1169](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1169)

***

### last\_attempt\_summary?

> `optional` **last\_attempt\_summary**: `string`

Defined in: [types/api.ts:1168](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1168)

***

### last\_attempted\_at?

> `optional` **last\_attempted\_at**: `string`

Defined in: [types/api.ts:1167](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1167)

***

### last\_seen\_at?

> `optional` **last\_seen\_at**: `string`

Defined in: [types/api.ts:1163](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1163)

***

### last\_seen\_execution\_id?

> `optional` **last\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1164](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1164)

***

### merchant\_name?

> `optional` **merchant\_name**: `string`

Defined in: [types/api.ts:1149](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1149)

***

### occurrence\_count?

> `optional` **occurrence\_count**: `number`

Defined in: [types/api.ts:1165](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1165)

***

### preflight?

> `optional` **preflight**: [`ReceiptHealthIssuePreflight`](ReceiptHealthIssuePreflight.md)

Defined in: [types/api.ts:1160](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1160)

***

### receipt\_type?

> `optional` **receipt\_type**: `"itemized"` \| `"service"` \| `"terminal"`

Defined in: [types/api.ts:1150](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1150)

***

### result?

> `optional` **result**: `string`

Defined in: [types/api.ts:1157](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1157)

***

### state?

> `optional` **state**: [`ReceiptHealthIssueState`](../type-aliases/ReceiptHealthIssueState.md)

Defined in: [types/api.ts:1159](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1159)

***

### suppression\_fingerprint?

> `optional` **suppression\_fingerprint**: `string`

Defined in: [types/api.ts:1170](https://github.com/tnorlund/Portfolio/blob/fcde25575967ec0a76fcc4aee11880f2a2a9d9ad/portfolio/types/api.ts#L1170)
