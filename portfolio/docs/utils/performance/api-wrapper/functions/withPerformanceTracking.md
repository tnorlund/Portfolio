[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/api-wrapper](../README.md) / withPerformanceTracking

# Function: withPerformanceTracking()

> **withPerformanceTracking**\<`T`, `R`\>(`fn`, `endpoint`): [`APIFunction`](../type-aliases/APIFunction.md)\<`T`, `R`\>

Defined in: [utils/performance/api-wrapper.ts:8](https://github.com/tnorlund/Portfolio/blob/65c9f803fe6011fe684788b7c0d13570a773c13b/portfolio/utils/performance/api-wrapper.ts#L8)

Wraps an API function to automatically track its performance

## Type Parameters

### T

`T` *extends* `any`[]

### R

`R`

## Parameters

### fn

[`APIFunction`](../type-aliases/APIFunction.md)\<`T`, `R`\>

### endpoint

`string`

## Returns

[`APIFunction`](../type-aliases/APIFunction.md)\<`T`, `R`\>
