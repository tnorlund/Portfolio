[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReceiptHealthLedgerIssue

# Interface: ReceiptHealthLedgerIssue

Defined in: [types/api.ts:1065](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1065)

## Properties

### check\_id

> **check\_id**: `"merchant_identity"` \| `"receipt_format"` \| `"financial_math"`

Defined in: [types/api.ts:1074](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1074)

***

### check\_title

> **check\_title**: `string`

Defined in: [types/api.ts:1075](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1075)

***

### evidence

> **evidence**: `Record`\<`string`, `unknown`\>[]

Defined in: [types/api.ts:1081](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1081)

***

### execution\_id

> **execution\_id**: `string`

Defined in: [types/api.ts:1068](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1068)

***

### fingerprint

> **fingerprint**: `string`

Defined in: [types/api.ts:1067](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1067)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:1070](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1070)

***

### issue\_id

> **issue\_id**: `string`

Defined in: [types/api.ts:1066](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1066)

***

### issue\_type

> **issue\_type**: `string`

Defined in: [types/api.ts:1078](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1078)

***

### message

> **message**: `string`

Defined in: [types/api.ts:1079](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1079)

***

### observed\_at

> **observed\_at**: `string`

Defined in: [types/api.ts:1069](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1069)

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:1071](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1071)

***

### status

> **status**: [`ReceiptHealthStatus`](../type-aliases/ReceiptHealthStatus.md)

Defined in: [types/api.ts:1077](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1077)

***

### validator

> **validator**: `"financial_math"` \| `"place_validation"` \| `"format_validation"`

Defined in: [types/api.ts:1076](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1076)

***

### attempt\_count?

> `optional` **attempt\_count**: `number`

Defined in: [types/api.ts:1089](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1089)

***

### first\_seen\_at?

> `optional` **first\_seen\_at**: `string`

Defined in: [types/api.ts:1084](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1084)

***

### first\_seen\_execution\_id?

> `optional` **first\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1085](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1085)

***

### known\_limitation\_reason?

> `optional` **known\_limitation\_reason**: `string`

Defined in: [types/api.ts:1092](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1092)

***

### last\_attempt\_summary?

> `optional` **last\_attempt\_summary**: `string`

Defined in: [types/api.ts:1091](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1091)

***

### last\_attempted\_at?

> `optional` **last\_attempted\_at**: `string`

Defined in: [types/api.ts:1090](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1090)

***

### last\_seen\_at?

> `optional` **last\_seen\_at**: `string`

Defined in: [types/api.ts:1086](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1086)

***

### last\_seen\_execution\_id?

> `optional` **last\_seen\_execution\_id**: `string`

Defined in: [types/api.ts:1087](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1087)

***

### merchant\_name?

> `optional` **merchant\_name**: `string`

Defined in: [types/api.ts:1072](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1072)

***

### occurrence\_count?

> `optional` **occurrence\_count**: `number`

Defined in: [types/api.ts:1088](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1088)

***

### preflight?

> `optional` **preflight**: [`ReceiptHealthIssuePreflight`](ReceiptHealthIssuePreflight.md)

Defined in: [types/api.ts:1083](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1083)

***

### receipt\_type?

> `optional` **receipt\_type**: `"itemized"` \| `"service"` \| `"terminal"`

Defined in: [types/api.ts:1073](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1073)

***

### result?

> `optional` **result**: `string`

Defined in: [types/api.ts:1080](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1080)

***

### state?

> `optional` **state**: [`ReceiptHealthIssueState`](../type-aliases/ReceiptHealthIssueState.md)

Defined in: [types/api.ts:1082](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1082)

***

### suppression\_fingerprint?

> `optional` **suppression\_fingerprint**: `string`

Defined in: [types/api.ts:1093](https://github.com/tnorlund/Portfolio/blob/c1e73805612f8fa5b8e467c2305dfc954dcccf54/portfolio/types/api.ts#L1093)
