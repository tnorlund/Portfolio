[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/api-wrapper](../README.md) / withPerformanceTracking

# Function: withPerformanceTracking()

> **withPerformanceTracking**\<`T`, `R`\>(`fn`, `endpoint`): [`APIFunction`](../type-aliases/APIFunction.md)\<`T`, `R`\>

Defined in: [utils/performance/api-wrapper.ts:8](https://github.com/tnorlund/Portfolio/blob/ef7e49d7339717ecd3108c55ed4668400d2c9488/portfolio/utils/performance/api-wrapper.ts#L8)

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
