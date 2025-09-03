[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/logger](../README.md) / PerformanceLogger

# Class: PerformanceLogger

Defined in: [utils/performance/logger.ts:10](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L10)

## Constructors

### Constructor

> **new PerformanceLogger**(): `PerformanceLogger`

#### Returns

`PerformanceLogger`

## Methods

### clear()

> **clear**(): `void`

Defined in: [utils/performance/logger.ts:109](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L109)

#### Returns

`void`

***

### downloadReport()

> **downloadReport**(): `void`

Defined in: [utils/performance/logger.ts:244](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L244)

#### Returns

`void`

***

### generateReport()

> **generateReport**(): `string`

Defined in: [utils/performance/logger.ts:118](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L118)

#### Returns

`string`

***

### getLogs()

> **getLogs**(): [`PerformanceLog`](../interfaces/PerformanceLog.md)[]

Defined in: [utils/performance/logger.ts:105](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L105)

#### Returns

[`PerformanceLog`](../interfaces/PerformanceLog.md)[]

***

### loadFromLocalStorage()

> **loadFromLocalStorage**(): `void`

Defined in: [utils/performance/logger.ts:48](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L48)

#### Returns

`void`

***

### log()

> **log**(`metrics`): `void`

Defined in: [utils/performance/logger.ts:14](https://github.com/tnorlund/Portfolio/blob/a18583ee921f6a4fb101dcba418904f87a60b395/portfolio/utils/performance/logger.ts#L14)

#### Parameters

##### metrics

[`PerformanceMetrics`](../../monitor/interfaces/PerformanceMetrics.md)

#### Returns

`void`
