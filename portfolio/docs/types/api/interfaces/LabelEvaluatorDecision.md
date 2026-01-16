[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:472](https://github.com/tnorlund/Portfolio/blob/8fb62a88c21fbbf413f528a44bc4d95bd0ecf887/portfolio/types/api.ts#L472)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:473](https://github.com/tnorlund/Portfolio/blob/8fb62a88c21fbbf413f528a44bc4d95bd0ecf887/portfolio/types/api.ts#L473)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:475](https://github.com/tnorlund/Portfolio/blob/8fb62a88c21fbbf413f528a44bc4d95bd0ecf887/portfolio/types/api.ts#L475)

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

Defined in: [types/api.ts:481](https://github.com/tnorlund/Portfolio/blob/8fb62a88c21fbbf413f528a44bc4d95bd0ecf887/portfolio/types/api.ts#L481)

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

Defined in: [types/api.ts:474](https://github.com/tnorlund/Portfolio/blob/8fb62a88c21fbbf413f528a44bc4d95bd0ecf887/portfolio/types/api.ts#L474)
