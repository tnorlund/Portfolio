# Week 4: Add Streaming and Observability

## Overview

This week adds real-time streaming updates and comprehensive observability to the receipt labeling pipeline. Users can watch the labeling process unfold in real-time while operators gain deep insights into system performance.

## Goals

1. Implement streaming updates for real-time progress tracking
2. Add comprehensive observability with OpenTelemetry
3. Create WebSocket API for frontend integration
4. Build monitoring dashboards and alerts

## Implementation Plan

### Step 1: Define Streaming Events

```python
# receipt_label/streaming/events.py

from typing import Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class EventType(Enum):
    """Types of events that can be streamed."""
    # Lifecycle events
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETED = "processing_completed"
    PROCESSING_FAILED = "processing_failed"

    # Tool events
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"

    # Result events
    FIELD_LABELED = "field_labeled"
    LINE_ITEM_FOUND = "line_item_found"
    CONFIDENCE_UPDATE = "confidence_update"
    VALIDATION_RESULT = "validation_result"

class StreamEvent(BaseModel):
    """Base class for all streaming events."""
    event_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.now)
    receipt_id: str
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ToolEvent(StreamEvent):
    """Event for tool execution updates."""
    tool_name: str
    status: str  # started, running, completed, failed
    progress: Optional[float] = None  # 0-100
    message: Optional[str] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None

class FieldLabelEvent(StreamEvent):
    """Event when a field is labeled."""
    field_name: str
    value: str
    confidence: float
    reasoning: Optional[str] = None
    word_ids: List[int] = Field(default_factory=list)

class LineItemEvent(StreamEvent):
    """Event when a line item is found."""
    description: str
    quantity: Optional[Dict[str, Any]] = None
    price: Optional[Dict[str, Any]] = None
    line_ids: List[int] = Field(default_factory=list)
```

### Step 2: Implement Event Emitter

```python
# receipt_label/streaming/emitter.py

from typing import Optional, Callable, List, Dict, Any
import asyncio
from collections import defaultdict
import logging

EventHandler = Callable[[StreamEvent], None]
AsyncEventHandler = Callable[[StreamEvent], asyncio.Task]

class EventEmitter:
    """Manages event streaming and distribution."""

    def __init__(self):
        self.handlers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self.async_handlers: Dict[EventType, List[AsyncEventHandler]] = defaultdict(list)
        self.logger = logging.getLogger("event_emitter")
        self._buffer: List[StreamEvent] = []
        self._buffering = False

    def on(self, event_type: EventType, handler: Union[EventHandler, AsyncEventHandler]):
        """Register an event handler."""
        if asyncio.iscoroutinefunction(handler):
            self.async_handlers[event_type].append(handler)
        else:
            self.handlers[event_type].append(handler)

    def emit(self, event: StreamEvent):
        """Emit an event to all registered handlers."""
        if self._buffering:
            self._buffer.append(event)
            return

        # Sync handlers
        for handler in self.handlers[event.event_type]:
            try:
                handler(event)
            except Exception as e:
                self.logger.error(f"Error in handler: {e}")

        # Async handlers
        for handler in self.async_handlers[event.event_type]:
            asyncio.create_task(self._call_async_handler(handler, event))

    async def _call_async_handler(self, handler: AsyncEventHandler, event: StreamEvent):
        """Call async handler with error handling."""
        try:
            await handler(event)
        except Exception as e:
            self.logger.error(f"Error in async handler: {e}")

    def start_buffering(self):
        """Start buffering events instead of emitting."""
        self._buffering = True

    def flush_buffer(self):
        """Emit all buffered events."""
        self._buffering = False
        for event in self._buffer:
            self.emit(event)
        self._buffer.clear()

# Global emitter instance
emitter = EventEmitter()
```

### Step 3: Add Streaming to Tools

```python
# receipt_label/tools/base.py (updated)

class Tool(ABC, Generic[InputType, OutputType]):
    """Updated base class with streaming support."""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self.logger = logging.getLogger(f"tool.{name}")
        self.emitter = emitter  # Use global emitter

    def __call__(self, context: ReceiptContext) -> ReceiptContext:
        """Execute the tool with streaming updates."""
        start_time = time.time()

        # Emit start event
        self.emitter.emit(ToolEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.TOOL_STARTED,
            receipt_id=context.receipt_id,
            tool_name=self.name,
            status="started"
        ))

        try:
            # Validate input
            if not self.validate_input(context):
                raise ValueError(f"Invalid input for tool {self.name}")

            # Execute tool with progress tracking
            result = self.execute_with_progress(context)

            # Emit completion
            duration_ms = (time.time() - start_time) * 1000
            self.emitter.emit(ToolEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.TOOL_COMPLETED,
                receipt_id=context.receipt_id,
                tool_name=self.name,
                status="completed",
                duration_ms=duration_ms
            ))

            return result

        except Exception as e:
            # Emit failure
            self.emitter.emit(ToolEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.TOOL_FAILED,
                receipt_id=context.receipt_id,
                tool_name=self.name,
                status="failed",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000
            ))
            raise

    def execute_with_progress(self, context: ReceiptContext) -> ReceiptContext:
        """Execute with progress updates."""
        # Default implementation without progress
        return self.execute(context)

    def emit_progress(self, context: ReceiptContext, progress: float, message: str = ""):
        """Emit progress update."""
        self.emitter.emit(ToolEvent(
            event_id=str(uuid.uuid4()),
            event_type=EventType.TOOL_PROGRESS,
            receipt_id=context.receipt_id,
            tool_name=self.name,
            status="running",
            progress=progress,
            message=message
        ))
```

### Step 4: Implement WebSocket API

```python
# receipt_label/api/websocket.py

from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set
import json
import asyncio
import uuid

class ConnectionManager:
    """Manages WebSocket connections for streaming."""

    def __init__(self):
        # Map receipt_id to set of connections
        self.active_connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # Map connection to receipt_ids
        self.connection_receipts: Dict[WebSocket, Set[str]] = defaultdict(set)

    async def connect(self, websocket: WebSocket, receipt_id: str):
        """Accept new connection."""
        await websocket.accept()
        self.active_connections[receipt_id].add(websocket)
        self.connection_receipts[websocket].add(receipt_id)

    def disconnect(self, websocket: WebSocket):
        """Remove connection."""
        # Remove from all receipt subscriptions
        for receipt_id in self.connection_receipts[websocket]:
            self.active_connections[receipt_id].discard(websocket)
        del self.connection_receipts[websocket]

    async def send_event(self, receipt_id: str, event: StreamEvent):
        """Send event to all connections watching this receipt."""
        if receipt_id not in self.active_connections:
            return

        # Serialize event
        message = event.json()

        # Send to all connections
        disconnected = set()
        for connection in self.active_connections[receipt_id]:
            try:
                await connection.send_text(message)
            except:
                disconnected.add(connection)

        # Clean up disconnected
        for conn in disconnected:
            self.disconnect(conn)

# Global connection manager
manager = ConnectionManager()

# Register event handlers
async def websocket_event_handler(event: StreamEvent):
    """Forward events to WebSocket connections."""
    await manager.send_event(event.receipt_id, event)

# Register all event types
for event_type in EventType:
    emitter.on(event_type, websocket_event_handler)
```

### Step 5: FastAPI WebSocket Endpoint

```python
# receipt_label/api/app.py

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio

app = FastAPI(title="Receipt Labeling API")

# CORS for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws/receipt/{receipt_id}")
async def websocket_endpoint(websocket: WebSocket, receipt_id: str):
    """WebSocket endpoint for real-time receipt processing updates."""
    await manager.connect(websocket, receipt_id)

    try:
        # Send initial connection event
        await websocket.send_json({
            "event_type": "connected",
            "receipt_id": receipt_id,
            "message": "Connected to receipt processing stream"
        })

        # Keep connection alive
        while True:
            # Wait for client messages (ping/pong)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/label-receipt/{receipt_id}")
async def label_receipt_streaming(receipt_id: str, request: LabelingRequest):
    """Start receipt labeling with streaming updates."""
    # Start processing in background
    asyncio.create_task(process_receipt_async(receipt_id, request))

    # Return immediately
    return {
        "receipt_id": receipt_id,
        "status": "processing",
        "websocket_url": f"/ws/receipt/{receipt_id}"
    }
```

### Step 6: Add OpenTelemetry Integration

```python
# receipt_label/observability/tracing.py

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import Status, StatusCode
import os

# Configure tracer
def setup_tracing(service_name: str = "receipt-labeling"):
    """Configure OpenTelemetry tracing."""
    # Set up the tracer provider
    provider = TracerProvider()
    trace.set_tracer_provider(provider)

    # Configure OTLP exporter
    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317"),
        insecure=True
    )

    # Add span processor
    span_processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(span_processor)

    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)

    return trace.get_tracer(service_name)

# Global tracer
tracer = setup_tracing()

# Traced tool wrapper
class TracedTool(Tool):
    """Tool wrapper with distributed tracing."""

    def execute_with_progress(self, context: ReceiptContext) -> ReceiptContext:
        """Execute with tracing."""
        with tracer.start_as_current_span(f"tool.{self.name}") as span:
            # Add attributes
            span.set_attribute("receipt.id", context.receipt_id)
            span.set_attribute("tool.name", self.name)

            try:
                result = super().execute_with_progress(context)
                span.set_status(Status(StatusCode.OK))
                return result

            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise
```

### Step 7: Frontend Integration Example

```typescript
// Frontend WebSocket client example

interface StreamEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  receipt_id: string;
  [key: string]: any;
}

class ReceiptProcessingStream {
  private ws: WebSocket | null = null;
  private eventHandlers: Map<string, ((event: StreamEvent) => void)[]> = new Map();

  connect(receiptId: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const wsUrl = `${process.env.NEXT_PUBLIC_WS_URL}/ws/receipt/${receiptId}`;
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log('Connected to receipt processing stream');
        resolve();
      };

      this.ws.onmessage = (event) => {
        const streamEvent: StreamEvent = JSON.parse(event.data);
        this.handleEvent(streamEvent);
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        reject(error);
      };

      this.ws.onclose = () => {
        console.log('Disconnected from stream');
      };
    });
  }

  on(eventType: string, handler: (event: StreamEvent) => void) {
    if (!this.eventHandlers.has(eventType)) {
      this.eventHandlers.set(eventType, []);
    }
    this.eventHandlers.get(eventType)!.push(handler);
  }

  private handleEvent(event: StreamEvent) {
    const handlers = this.eventHandlers.get(event.event_type) || [];
    handlers.forEach(handler => handler(event));
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

// React component example
export function ReceiptProcessingView({ receiptId }: { receiptId: string }) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [currentTool, setCurrentTool] = useState<string>('');
  const [progress, setProgress] = useState<number>(0);

  useEffect(() => {
    const stream = new ReceiptProcessingStream();

    // Connect to stream
    stream.connect(receiptId).then(() => {
      // Listen for events
      stream.on('tool_started', (event) => {
        setCurrentTool(event.tool_name);
        setProgress(0);
      });

      stream.on('tool_progress', (event) => {
        setProgress(event.progress || 0);
      });

      stream.on('field_labeled', (event) => {
        setEvents(prev => [...prev, event]);
      });
    });

    return () => stream.disconnect();
  }, [receiptId]);

  return (
    <div>
      <h2>Processing Receipt</h2>
      {currentTool && (
        <div>
          <p>Current: {currentTool}</p>
          <ProgressBar value={progress} />
        </div>
      )}
      <Timeline events={events} />
    </div>
  );
}
```

### Step 8: Monitoring and Dashboards

```python
# receipt_label/observability/metrics.py

from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import Response
import time

# Define metrics
receipt_processing_total = Counter(
    'receipt_processing_total',
    'Total number of receipts processed',
    ['status']
)

receipt_processing_duration = Histogram(
    'receipt_processing_duration_seconds',
    'Receipt processing duration in seconds',
    ['tool']
)

active_processing = Gauge(
    'active_receipt_processing',
    'Number of receipts currently being processed'
)

tool_success_rate = Gauge(
    'tool_success_rate',
    'Success rate of each tool',
    ['tool_name']
)

# Instrument tools
class MetricsTool(TracedTool):
    """Tool with metrics collection."""

    def __call__(self, context: ReceiptContext) -> ReceiptContext:
        active_processing.inc()
        start_time = time.time()

        try:
            result = super().__call__(context)
            receipt_processing_total.labels(status='success').inc()
            return result

        except Exception as e:
            receipt_processing_total.labels(status='failure').inc()
            raise

        finally:
            duration = time.time() - start_time
            receipt_processing_duration.labels(tool=self.name).observe(duration)
            active_processing.dec()

# Prometheus endpoint
@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type="text/plain")
```

### Step 9: Grafana Dashboard Configuration

```json
{
  "dashboard": {
    "title": "Receipt Labeling Pipeline",
    "panels": [
      {
        "title": "Processing Rate",
        "targets": [
          {
            "expr": "rate(receipt_processing_total[5m])",
            "legendFormat": "{{status}}"
          }
        ]
      },
      {
        "title": "Tool Performance",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, receipt_processing_duration_seconds)",
            "legendFormat": "p95 - {{tool}}"
          }
        ]
      },
      {
        "title": "Active Processing",
        "targets": [
          {
            "expr": "active_receipt_processing"
          }
        ]
      },
      {
        "title": "Tool Success Rates",
        "targets": [
          {
            "expr": "tool_success_rate",
            "legendFormat": "{{tool_name}}"
          }
        ]
      }
    ]
  }
}
```

## Testing Strategy

### WebSocket Testing

```python
# test_websocket.py

async def test_websocket_streaming():
    """Test WebSocket event streaming."""
    async with websockets.connect(f"ws://localhost:8000/ws/receipt/test-123") as ws:
        # Start processing
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/api/label-receipt/test-123",
                json={"image_url": "s3://test.jpg"}
            )

        # Receive events
        events = []
        async for message in ws:
            event = json.loads(message)
            events.append(event)

            if event["event_type"] == "processing_completed":
                break

        # Verify event sequence
        event_types = [e["event_type"] for e in events]
        assert "processing_started" in event_types
        assert "tool_started" in event_types
        assert "processing_completed" in event_types
```

## Performance Considerations

1. **Event Batching**: Buffer events and send in batches to reduce WebSocket overhead
2. **Compression**: Enable WebSocket compression for large payloads
3. **Circuit Breaking**: Implement circuit breakers for downstream services
4. **Rate Limiting**: Limit events per second to prevent client overwhelm

## Success Criteria

- WebSocket latency <50ms for event delivery
- Support 1000+ concurrent WebSocket connections
- Complete observability with <1% trace sampling overhead
- 99.9% uptime for streaming infrastructure

## Next Steps

- Implement event replay for debugging
- Add event filtering on client side
- Create mobile SDK for streaming
- Build automated anomaly detection from metrics
