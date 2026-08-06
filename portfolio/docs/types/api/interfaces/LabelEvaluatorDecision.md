[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/b346ea8d8d2a3c2ae05debe93cd2668b57581329/portfolio/types/api.ts#L614)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/b346ea8d8d2a3c2ae05debe93cd2668b57581329/portfolio/types/api.ts#L615)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:617](https://github.com/tnorlund/Portfolio/blob/b346ea8d8d2a3c2ae05debe93cd2668b57581329/portfolio/types/api.ts#L617)

#### current\_label

> **current\_label**: `string`

#### line\_id

> **line\_id**: `number`

#### word\_id

> **word\_id**: `number`

#### word\_text

> **word\_text**: `string`

***

### llm\_review

> **llm\_review**: `object`

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/b346ea8d8d2a3c2ae05debe93cd2668b57581329/portfolio/types/api.ts#L623)

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

Defined in: [types/api.ts:616](https://github.com/tnorlund/Portfolio/blob/b346ea8d8d2a3c2ae05debe93cd2668b57581329/portfolio/types/api.ts#L616)
