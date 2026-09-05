# Personal planner

A paper-inspired Week/Month/Year planner with direct editing and real MCP tools.
Python HTTP and MCP transports share one validated command service backed by
DynamoDB. The Next.js page lives at `/planner` in the existing `portfolio` app.

The first release runs locally. It does not include a hosted API, Cognito sign-in,
a built-in LLM, or scheduled agents. No AWS resources are created by these steps.

## Run locally

Use Python 3.13, Node 22, and DynamoDB Local. From the repository root:

```sh
python3.13 -m venv .venv-planner
.venv-planner/bin/pip install -e 'planner[test,lint]'
```

Start a persistent DynamoDB Local instance in a separate terminal. For example,
with Docker (mount a writable directory for persistence):

```sh
mkdir -p .local-qa-out/dynamodb/data
docker run --rm --name personal-planner-dynamo \
  -p 127.0.0.1:8317:8000 \
  -v "$PWD/.local-qa-out/dynamodb/data:/home/dynamodblocal/data" \
  amazon/dynamodb-local:latest \
  -jar DynamoDBLocal.jar -sharedDb -dbPath ./data -disableTelemetry
```

Alternatively use the [official DynamoDB Local JAR and setup instructions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.DownloadingAndRunning.html).
The local evaluation used the official JAR, Java 17, persistent `-dbPath`, and port
8317. This service has no user authentication; keep it local.

Start the planner API in another terminal:

```sh
export PLANNER_ENV=local
export PLANNER_TABLE=PlannerLocal
export PLANNER_ENDPOINT=http://127.0.0.1:8317
.venv-planner/bin/planner-api --create-local-table
```

It binds only to 127.0.0.1:4317. To try fictional content, add
`--demo-week 2026-09-07`. This is opt-in and repeatable; rerunning the seed does not
reset completed or edited work. Omitting it starts an empty planner.

In another terminal:

```sh
cd portfolio
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3400
```

Open [the planner](http://127.0.0.1:3400/planner), or
[the fictional example week](http://127.0.0.1:3400/planner?date=2026-09-07&view=week).
The API allows local frontend origins on ports 3000 and 3400. Stop processes with
Ctrl-C. Restart with the same table name and DynamoDB data directory to retain work.

## Connect an agent

Register `planner-mcp` as a local stdio server in an MCP-capable client. Replace
`/absolute/path/to/Portfolio` with this checkout path and use the same table and
endpoint as the HTTP API:

```json
{
  "mcpServers": {
    "planner": {
      "command": "/absolute/path/to/Portfolio/.venv-planner/bin/planner-mcp",
      "env": {
        "PLANNER_ENV": "local",
        "PLANNER_TABLE": "PlannerLocal",
        "PLANNER_ENDPOINT": "http://127.0.0.1:8317"
      }
    }
  }
}
```

The supplied configuration shape is for clients that accept `mcpServers` JSON;
other clients expose equivalent command/environment fields. No client registration
is installed automatically.

Tools:

- `get_planner`: read the current snapshot, preferences, and local date.
- `apply_change(command, request_id)`: apply a requested change.
- `propose_changes(title, rationale, changes, request_id)`: save a suggestion for
  acceptance on the page, without changing scheduled work.
- `resolve_proposal(proposal_id, decision, request_id)`: accept/reject on request.

Try: “Read my planner and suggest where to put two unfinished tasks next week.
Keep my appointments fixed, consider the time estimates, and explain your choices.”
The connected agent does the reasoning; the MCP server validates and persists its
commands. The page refreshes within the polling interval after the data changes.

## Command examples

Create with a stable request id for this logical operation:

```json
{
  "request_id": "create-project-draft-001",
  "command": {
    "action": "save_item",
    "text": "Draft the project outline",
    "kind": "task",
    "date": "2026-09-08",
    "due_date": "2026-09-11",
    "estimate_minutes": 45
  }
}
```

POST that envelope to `http://127.0.0.1:4317/planner/api/commands`, with
`Content-Type: application/json`. MCP `apply_change` takes the same two fields.
The response contains `{result, version, env, table}`. Updates require the returned
`id` and current `revision`:

```json
{
  "action": "save_item",
  "id": "<returned-id>",
  "revision": 1,
  "date": "2026-09-09",
  "done": true
}
```

A new operation needs a new request id. Retry an uncertain operation with exactly
the same id and command. A reused id with different content is rejected. Failed
validation and stale revisions do not partially apply changes.

Supported commands: `save_item`, `save_area`, `save_routine`, `save_week`,
`preferences`, `plan_week`, `close_week`, `batch`, `propose`, `resolve_proposal`.
The [specification](../docs/plans/PLANNER_SPEC_2026-09-05.md) describes their behavior;
`planner/service.py` defines accepted fields and validation.

## DynamoDB structure

`entities/record.py` follows Portfolio's dataclass, key, `to_item`/`from_item`, and
AttributeValue conventions. `data/client.py` takes an explicit table name and owns
all transactions. HTTP and MCP never bypass the shared service.

Each canonical item is a separate row with PK `PLANNER`, SK `<TYPE>#<id>`, and TYPE.
A `GSITYPE` index matches the existing entity-discovery convention. The application
uses strongly consistent paginated Query/GetItem reads, not Scan or index reads.
The clock, changed rows, and idempotency receipt commit atomically. Snapshot reads
retry if a concurrent transaction crosses the read.

The first version is for one private owner. It reads the full planner snapshot;
it does not yet provide history retention or multi-user partitioning. Operation
receipts are retained for durable retry semantics. Commands affect at most 98
canonical rows after reserving actions for the clock and receipt, and must fit the
[DynamoDB transaction limits](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html).
Oversized changes fail before any write.

`PLANNER_ENV=local` requires a loopback HTTP endpoint and uses dummy credentials.
The dev adapter requires explicit `PLANNER_ALLOW_DEV=1`, no endpoint override, and
AWS account 681647709217. It has not been deployed or live-tested. Production is
rejected. Follow repository AGENTS.md before any separately requested dev work.

## Evaluate

From the repository root:

```sh
.venv-planner/bin/python -m black --check planner
.venv-planner/bin/python -m isort --check-only planner
.venv-planner/bin/python -m pytest -c planner/pyproject.toml \
  --confcutdir=planner planner/tests
```

Include the real DynamoDB Local transaction and stdio MCP-to-HTTP tests:

```sh
PLANNER_TEST_ENDPOINT=http://127.0.0.1:8317 \
  .venv-planner/bin/python -m pytest -c planner/pyproject.toml \
  --confcutdir=planner planner/tests
```

Wire tests create and delete uniquely named tables on the explicit loopback
endpoint. They do not touch the example table or AWS. Ordinary tests use moto.

Frontend checks, from `portfolio/`:

```sh
npm test -- --runInBand
npx tsc --noEmit
npx eslint pages/planner.tsx pages/_app.tsx components/planner services/planner
npm run build
```

Stop the Next dev server before a production build because both use `.next`.
Browser evaluation should cover direct edits and dates, completion/reopening,
agent writes appearing without a reload, proposal acceptance, Month/Year navigation,
mobile layout, and connection/conflict handling. Compare the rendered weekly view
against the paper reference before treating visual work as complete.
