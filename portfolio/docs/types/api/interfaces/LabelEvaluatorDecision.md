[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:499](https://github.com/tnorlund/Portfolio/blob/00aa6c5174ef74d2d8e200d1813868d6be870a62/portfolio/types/api.ts#L499)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:500](https://github.com/tnorlund/Portfolio/blob/00aa6c5174ef74d2d8e200d1813868d6be870a62/portfolio/types/api.ts#L500)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:502](https://github.com/tnorlund/Portfolio/blob/00aa6c5174ef74d2d8e200d1813868d6be870a62/portfolio/types/api.ts#L502)

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

Defined in: [types/api.ts:508](https://github.com/tnorlund/Portfolio/blob/00aa6c5174ef74d2d8e200d1813868d6be870a62/portfolio/types/api.ts#L508)

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

Defined in: [types/api.ts:501](https://github.com/tnorlund/Portfolio/blob/00aa6c5174ef74d2d8e200d1813868d6be870a62/portfolio/types/api.ts#L501)
