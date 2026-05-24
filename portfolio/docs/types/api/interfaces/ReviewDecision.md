[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / ReviewDecision

# Interface: ReviewDecision

Defined in: [types/api.ts:530](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L530)

## Properties

### consensus\_score

> **consensus\_score**: `number`

Defined in: [types/api.ts:533](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L533)

***

### evidence

> **evidence**: [`ReviewEvidence`](ReviewEvidence.md)[]

Defined in: [types/api.ts:545](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L545)

***

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:531](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L531)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:535](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L535)

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

Defined in: [types/api.ts:546](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L546)

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

Defined in: [types/api.ts:532](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L532)

***

### similar\_word\_count

> **similar\_word\_count**: `number`

Defined in: [types/api.ts:534](https://github.com/tnorlund/Portfolio/blob/858ca6a52034bc7166e1a67147da5769db41b84d/portfolio/types/api.ts#L534)
