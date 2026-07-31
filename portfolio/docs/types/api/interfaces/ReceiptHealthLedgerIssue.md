[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthLedgerIssue

# Interface: ReceiptHealthLedgerIssue

Defined in: [types/api.ts:1245](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1245)

## Properties

### check\_id

> **check\_id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1254](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1254)

***

### check\_title

> **check\_title**: `string`

Defined in: [types/api.ts:1255](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1255)

***

### evidence

> **evidence**: `Record`\<`string`, `unknown`\>[]

Defined in: [types/api.ts:1261](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1261)

***

### execution\_id

> **execution\_id**: `string`

Defined in: [types/api.ts:1248](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1248)

***

### fingerprint

> **fingerprint**: `string`

Defined in: [types/api.ts:1247](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1247)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:1250](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1250)

***

### issue\_id

> **issue\_id**: `string`

Defined in: [types/api.ts:1246](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1246)

***

### issue\_type

> **issue\_type**: `string`

Defined in: [types/api.ts:1258](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1258)

***

### message

> **message**: `string`

Defined in: [types/api.ts:1259](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1259)

***

### observed\_at

> **observed\_at**: `string`

Defined in: [types/api.ts:1249](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1249)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:1251](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1251)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1257](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1257)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1256](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1256)

***

### attempt\_count?

> `optional` **attempt\_count**: `number`

Defined in: [types/api.ts:1269](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1269)

***

### first\_seen\_at?

> `optional` **first\_seen\_at**: `string`

Defined in: [types/api.ts:1264](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1264)

***

### first\_seen\_execution\_id?

> `optional` **first\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1265](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1265)

***

### known\_limitation\_reason?

> `optional` **known\_limitation\_reason**: `string`

Defined in: [types/api.ts:1272](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1272)

***

### last\_attempt\_summary?

> `optional` **last\_attempt\_summary**: `string`

Defined in: [types/api.ts:1271](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1271)

***

### last\_attempted\_at?

> `optional` **last\_attempted\_at**: `string`

Defined in: [types/api.ts:1270](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1270)

***

### last\_seen\_at?

> `optional` **last\_seen\_at**: `string`

Defined in: [types/api.ts:1266](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1266)

***

### last\_seen\_execution\_id?

> `optional` **last\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1267](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1267)

***

### merchant\_name?

> `optional` **merchant\_name**: `string`

Defined in: [types/api.ts:1252](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1252)

***

### occurrence\_count?

> `optional` **occurrence\_count**: `number`

Defined in: [types/api.ts:1268](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1268)

***

### preflight?

> `optional` **preflight**: [`ReceiptHealthIssuePreflight`](ReceiptHealthIssuePreflight.md)

Defined in: [types/api.ts:1263](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1263)

***

### receipt\_type?

> `optional` **receipt\_type**: `"itemized"` \| `"service"` \| `"terminal"`

Defined in: [types/api.ts:1253](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1253)

***

### result?

> `optional` **result**: `string`

Defined in: [types/api.ts:1260](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1260)

***

### state?

> `optional` **state**: [`ReceiptHealthIssueState`](../type-aliases/ReceiptHealthIssueState.md)

Defined in: [types/api.ts:1262](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1262)

***

### suppression\_fingerprint?

> `optional` **suppression\_fingerprint**: `string`

Defined in: [types/api.ts:1273](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L1273)
