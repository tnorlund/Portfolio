[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReviewDecision

# Interface: ReviewDecision

Defined in: [types/api.ts:607](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L607)

## Properties

### consensus\_score

> **consensus\_score**: `number`

Defined in: [types/api.ts:610](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L610)

***

### evidence

> **evidence**: [`ReviewEvidence`](ReviewEvidence.md)[]

Defined in: [types/api.ts:622](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L622)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:608](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L608)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:612](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L612)

#### current\_label

> **current\_label**: `string` \| `null`

#### line\_id

> **line\_id**: `number`

#### reasoning

> **reasoning**: `string`

#### suggested\_label

> **suggested\_label**: `string`

#### suggested\_status

> **suggested\_status**: `string`

#### type

> **type**: `string`

#### word\_id

> **word\_id**: `number`

#### word\_text

> **word\_text**: `string`

***

### llm\_review

> **llm\_review**: `object`

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L623)

#### confidence

> **confidence**: `"high"` \| `"medium"` \| `"low"`

#### decision

> **decision**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"`

#### reasoning

> **reasoning**: `string`

#### suggested\_label

> **suggested\_label**: `string` \| `null`

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:609](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L609)

***

### similar\_word\_count

> **similar\_word\_count**: `number`

Defined in: [types/api.ts:611](https://github.com/tnorlund/Portfolio/blob/aacbb0e5f11d74efaba6eb3c404b1b2ca4ab9603/portfolio/types/api.ts#L611)
