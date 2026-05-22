[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:505](https://github.com/tnorlund/Portfolio/blob/d469f551bffef8d3381e288916a9b87d64717264/portfolio/types/api.ts#L505)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:506](https://github.com/tnorlund/Portfolio/blob/d469f551bffef8d3381e288916a9b87d64717264/portfolio/types/api.ts#L506)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:508](https://github.com/tnorlund/Portfolio/blob/d469f551bffef8d3381e288916a9b87d64717264/portfolio/types/api.ts#L508)

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

Defined in: [types/api.ts:514](https://github.com/tnorlund/Portfolio/blob/d469f551bffef8d3381e288916a9b87d64717264/portfolio/types/api.ts#L514)

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

Defined in: [types/api.ts:507](https://github.com/tnorlund/Portfolio/blob/d469f551bffef8d3381e288916a9b87d64717264/portfolio/types/api.ts#L507)
