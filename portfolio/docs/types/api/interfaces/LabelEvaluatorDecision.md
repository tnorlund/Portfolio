[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:388](https://github.com/tnorlund/Portfolio/blob/2f1c74408a58925872e7c3804282e7677cf88613/portfolio/types/api.ts#L388)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:389](https://github.com/tnorlund/Portfolio/blob/2f1c74408a58925872e7c3804282e7677cf88613/portfolio/types/api.ts#L389)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:391](https://github.com/tnorlund/Portfolio/blob/2f1c74408a58925872e7c3804282e7677cf88613/portfolio/types/api.ts#L391)

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

Defined in: [types/api.ts:397](https://github.com/tnorlund/Portfolio/blob/2f1c74408a58925872e7c3804282e7677cf88613/portfolio/types/api.ts#L397)

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

Defined in: [types/api.ts:390](https://github.com/tnorlund/Portfolio/blob/2f1c74408a58925872e7c3804282e7677cf88613/portfolio/types/api.ts#L390)
