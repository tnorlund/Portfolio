[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthLedgerIssue

# Interface: ReceiptHealthLedgerIssue

Defined in: [types/api.ts:1173](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1173)

## Properties

### check\_id

> **check\_id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1182](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1182)

***

### check\_title

> **check\_title**: `string`

Defined in: [types/api.ts:1183](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1183)

***

### evidence

> **evidence**: `Record`\<`string`, `unknown`\>[]

Defined in: [types/api.ts:1189](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1189)

***

### execution\_id

> **execution\_id**: `string`

Defined in: [types/api.ts:1176](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1176)

***

### fingerprint

> **fingerprint**: `string`

Defined in: [types/api.ts:1175](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1175)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:1178](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1178)

***

### issue\_id

> **issue\_id**: `string`

Defined in: [types/api.ts:1174](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1174)

***

### issue\_type

> **issue\_type**: `string`

Defined in: [types/api.ts:1186](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1186)

***

### message

> **message**: `string`

Defined in: [types/api.ts:1187](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1187)

***

### observed\_at

> **observed\_at**: `string`

Defined in: [types/api.ts:1177](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1177)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:1179](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1179)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1185](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1185)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1184](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1184)

***

### attempt\_count?

> `optional` **attempt\_count**: `number`

Defined in: [types/api.ts:1197](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1197)

***

### first\_seen\_at?

> `optional` **first\_seen\_at**: `string`

Defined in: [types/api.ts:1192](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1192)

***

### first\_seen\_execution\_id?

> `optional` **first\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1193](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1193)

***

### known\_limitation\_reason?

> `optional` **known\_limitation\_reason**: `string`

Defined in: [types/api.ts:1200](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1200)

***

### last\_attempt\_summary?

> `optional` **last\_attempt\_summary**: `string`

Defined in: [types/api.ts:1199](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1199)

***

### last\_attempted\_at?

> `optional` **last\_attempted\_at**: `string`

Defined in: [types/api.ts:1198](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1198)

***

### last\_seen\_at?

> `optional` **last\_seen\_at**: `string`

Defined in: [types/api.ts:1194](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1194)

***

### last\_seen\_execution\_id?

> `optional` **last\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1195](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1195)

***

### merchant\_name?

> `optional` **merchant\_name**: `string`

Defined in: [types/api.ts:1180](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1180)

***

### occurrence\_count?

> `optional` **occurrence\_count**: `number`

Defined in: [types/api.ts:1196](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1196)

***

### preflight?

> `optional` **preflight**: [`ReceiptHealthIssuePreflight`](ReceiptHealthIssuePreflight.md)

Defined in: [types/api.ts:1191](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1191)

***

### receipt\_type?

> `optional` **receipt\_type**: `"itemized"` \| `"service"` \| `"terminal"`

Defined in: [types/api.ts:1181](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1181)

***

### result?

> `optional` **result**: `string`

Defined in: [types/api.ts:1188](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1188)

***

### state?

> `optional` **state**: [`ReceiptHealthIssueState`](../type-aliases/ReceiptHealthIssueState.md)

Defined in: [types/api.ts:1190](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1190)

***

### suppression\_fingerprint?

> `optional` **suppression\_fingerprint**: `string`

Defined in: [types/api.ts:1201](https://github.com/tnorlund/Portfolio/blob/a35299f030d996e6439142416d052760ce385370/portfolio/types/api.ts#L1201)
