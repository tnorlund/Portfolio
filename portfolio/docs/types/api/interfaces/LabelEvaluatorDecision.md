[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:613](https://github.com/tnorlund/Portfolio/blob/735f2d99b9cc86911886f5f7a8692aa9483caa18/portfolio/types/api.ts#L613)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:614](https://github.com/tnorlund/Portfolio/blob/735f2d99b9cc86911886f5f7a8692aa9483caa18/portfolio/types/api.ts#L614)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:616](https://github.com/tnorlund/Portfolio/blob/735f2d99b9cc86911886f5f7a8692aa9483caa18/portfolio/types/api.ts#L616)

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

Defined in: [types/api.ts:622](https://github.com/tnorlund/Portfolio/blob/735f2d99b9cc86911886f5f7a8692aa9483caa18/portfolio/types/api.ts#L622)

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

Defined in: [types/api.ts:615](https://github.com/tnorlund/Portfolio/blob/735f2d99b9cc86911886f5f7a8692aa9483caa18/portfolio/types/api.ts#L615)
