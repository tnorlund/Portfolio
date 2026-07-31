[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:685](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L685)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:686](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L686)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:688](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L688)

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

Defined in: [types/api.ts:694](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L694)

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

Defined in: [types/api.ts:687](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L687)
