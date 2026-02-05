[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:479](https://github.com/tnorlund/Portfolio/blob/cb195f42fca0ad40641de0266ca1515ce9e6ad5c/portfolio/types/api.ts#L479)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:480](https://github.com/tnorlund/Portfolio/blob/cb195f42fca0ad40641de0266ca1515ce9e6ad5c/portfolio/types/api.ts#L480)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:482](https://github.com/tnorlund/Portfolio/blob/cb195f42fca0ad40641de0266ca1515ce9e6ad5c/portfolio/types/api.ts#L482)

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

Defined in: [types/api.ts:488](https://github.com/tnorlund/Portfolio/blob/cb195f42fca0ad40641de0266ca1515ce9e6ad5c/portfolio/types/api.ts#L488)

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

Defined in: [types/api.ts:481](https://github.com/tnorlund/Portfolio/blob/cb195f42fca0ad40641de0266ca1515ce9e6ad5c/portfolio/types/api.ts#L481)
