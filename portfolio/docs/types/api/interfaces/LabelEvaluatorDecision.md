[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:499](https://github.com/tnorlund/Portfolio/blob/74da542b8cb88f1fab07f5fbb272bbb46d32e1bc/portfolio/types/api.ts#L499)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:500](https://github.com/tnorlund/Portfolio/blob/74da542b8cb88f1fab07f5fbb272bbb46d32e1bc/portfolio/types/api.ts#L500)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:502](https://github.com/tnorlund/Portfolio/blob/74da542b8cb88f1fab07f5fbb272bbb46d32e1bc/portfolio/types/api.ts#L502)

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

Defined in: [types/api.ts:508](https://github.com/tnorlund/Portfolio/blob/74da542b8cb88f1fab07f5fbb272bbb46d32e1bc/portfolio/types/api.ts#L508)

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

Defined in: [types/api.ts:501](https://github.com/tnorlund/Portfolio/blob/74da542b8cb88f1fab07f5fbb272bbb46d32e1bc/portfolio/types/api.ts#L501)
