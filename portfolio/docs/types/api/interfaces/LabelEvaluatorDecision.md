[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:477](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L477)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:478](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L478)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:480](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L480)

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

Defined in: [types/api.ts:486](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L486)

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

Defined in: [types/api.ts:479](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L479)
