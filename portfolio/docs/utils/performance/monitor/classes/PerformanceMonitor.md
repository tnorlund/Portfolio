[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/monitor](../README.md) / PerformanceMonitor

# Class: PerformanceMonitor

Defined in: [utils/performance/monitor.ts:22](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L22)

## Constructors

### Constructor

> **new PerformanceMonitor**(): `PerformanceMonitor`

Defined in: [utils/performance/monitor.ts:28](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L28)

#### Returns

`PerformanceMonitor`

## Methods

### destroy()

> **destroy**(): `void`

Defined in: [utils/performance/monitor.ts:176](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L176)

#### Returns

`void`

***

### getMetrics()

> **getMetrics**(): [`PerformanceMetrics`](../interfaces/PerformanceMetrics.md)

Defined in: [utils/performance/monitor.ts:158](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L158)

#### Returns

[`PerformanceMetrics`](../interfaces/PerformanceMetrics.md)

***

### reset()

> **reset**(): `void`

Defined in: [utils/performance/monitor.ts:171](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L171)

#### Returns

`void`

***

### subscribe()

> **subscribe**(`callback`): () => `boolean`

Defined in: [utils/performance/monitor.ts:162](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L162)

#### Parameters

##### callback

(`metrics`) => `void`

#### Returns

> (): `boolean`

##### Returns

`boolean`

***

### trackAPICall()

> **trackAPICall**(`endpoint`, `duration`): `void`

Defined in: [utils/performance/monitor.ts:139](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L139)

#### Parameters

##### endpoint

`string`

##### duration

`number`

#### Returns

`void`

***

### trackComponentRender()

> **trackComponentRender**(`componentName`, `duration`): `void`

Defined in: [utils/performance/monitor.ts:128](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L128)

#### Parameters

##### componentName

`string`

##### duration

`number`

#### Returns

`void`

***

### trackImageLoad()

> **trackImageLoad**(`imageSrc`, `duration`): `void`

Defined in: [utils/performance/monitor.ts:150](https://github.com/tnorlund/Portfolio/blob/431b96d60484c033111a2bd49c67ccc06dcc1c23/portfolio/utils/performance/monitor.ts#L150)

#### Parameters

##### imageSrc

`string`

##### duration

`number`

#### Returns

`void`
