# Personal Planner: working specification

Date: 2026-09-05. Status: collaborative product revision with Tyler.

This document is being revised before implementation. Product decisions recorded
below supersede the original finance-specific assumptions. Engineering sections
that still depend on open product decisions are provisional, not a build contract.

Visual references: the five paper-planner photos in `/Users/tnorlund/Planner/`.
The earlier `PLAN_2026-09-05.html` in that folder is a historical concept; its fixed
rows, personal seed data, and delivery plan are not current requirements.

## Product direction

Build a general personal planner that helps Tyler decide what matters, give work a
place in the coming days, and follow through. Preserve the clarity and useful page
structure of the paper planner, with an agent able to help maintain the plan.

Taxes, budget, and savings were the initial motivating examples. They are ordinary
planner content, not permanent categories, required workflows, or the limits of
what the planner can organize. Do not infer broader product requirements or
personal records from the circumstances that prompted the planner.

Confirmed direction:

- Personal planning, with no classes, subjects, semesters, or after-school sections.
- The paper photos are the visual reference for the calendar and weekly pages.
- Category names and counts must not be encoded as finance-specific constants.
- Revise the product collaboratively before committing to an implementation plan.

Open decisions currently being discussed:

| Decision | Options under discussion | Status |
|---|---|---|
| Weekly organization | Optional focus-area rows, a list per day, or always-visible focus-area rows | Awaiting Tyler |
| Interaction | Direct editing plus an agent, agent-only changes, or editing plus proposals | Awaiting Tyler |
| Agent initiative | On request, proactive proposals, or automatic organization within preferences | Awaiting Tyler |

Suggested direction for the rest of this revision, not yet confirmed: Week, Month,
and Year navigation; a spacious weekly spread with its focus/goals/todos sidebar;
and an explicit usable layout for narrow screens.

---

## 0. Rules for the implementing agent

1. **Distinguish decisions from proposals.** During this revision, ask about product
   choices that materially change how Tyler uses the planner. Continue independent
   work while waiting. Do not treat suggested defaults or the original section 12
   assumptions as newly confirmed requirements.
2. **Stacked PRs, in the order in section 11.** Each PR must pass CI on its own. Do not
   merge; Claude and Tyler review and land. Parent PRs land with `--merge`, children
   with `--squash`.
3. **Touch nothing under `receipt_*`, `infra/routes/`, or the receipt MCP.** The planner
   is a sibling, not a modification. The only shared files you edit are
   `infra/__main__.py`, `infra/mcp_auth_gateway.py`, `.github/workflows/main.yml`,
   `portfolio/pages/`, `portfolio/components/`, `portfolio/services/api/`.
4. **Copy conventions from these files, do not invent new ones:**
   - Entity shape: `receipt_dynamo/receipt_dynamo/entities/receipt_section.py`
   - Client mixin: `receipt_dynamo/receipt_dynamo/data/_receipt_section.py`
   - Tests: `receipt_dynamo/tests/unit/test_receipt_section.py`,
     `receipt_dynamo/tests/integration/test__receipt_section.py`
   - Table: `infra/dynamo_db.py`
   - Small MCP server: `infra/ats_verification_inbox/lambdas/mcp.py` (stdlib, ~275 lines)
     and `/Users/tnorlund/receipts-email/server.py` if present. Not
     `scripts/receipt_mcp_server.py` (6,700 lines; wrong template).
   - Auth gateway route + scope: `infra/mcp_auth_gateway.py`, `infra/MCP_AUTH.md`,
     and PR #1503 (`a582e4dee`) which added an isolated MCP behind it.
   - Route Lambda factory: `infra/components/route_lambda.py` (but planner API routes
     mount on the auth gateway, not `api_gateway.py`; see section 7).
   - Site fetch layer: `portfolio/services/api/config.ts`, `portfolio/services/api/index.ts`.
5. **Python 3.13, line length 79, black + isort profile black.** Tests carry `unit` /
   `integration` markers. Integration tests use moto. Add `planner` to the
   `python-tests` matrix in `.github/workflows/main.yml`.
6. **No silent environment default.** The MCP server and the seed script exit non-zero
   with a clear message unless `PLANNER_ENV` is `dev` or `prod`. Every tool response
   includes `"env"` and `"table"`.
7. **Every write is conditional or transactional.** No unconditional `put_item` on an
   existing key. See section 3.4.
8. **Every write bumps the clock.** See section 3.5. This is what makes the front end
   update by itself. Forgetting it on one write path is a bug the front end will hide.
9. **Verify by executing.** Before opening each PR, run the exact commands in its
   acceptance list and paste the output into the PR description.

## 1. Goal and non-goals

**Goal.** A personal planner for organizing commitments, tasks, routines, and goals
across everyday life. A paper-inspired interface makes the current plan easy to
understand, and an agent helps create and maintain it. Agent changes appear on the
page without a manual refresh.

The weekly organization, direct-editing controls, and agent's default initiative
are open product decisions. Do not build the original fixed four-row layout or
read-only interaction model before those decisions are resolved.

**Non-goals for this build.**
- No school-specific concepts or required financial categories.
- No accounting, tax calculation, or savings-account system inside the planner.
  Optional connections can supply context later; the planner works without them.
- No automatic import of personal details from the earlier concept. Its example
  dates, amounts, and tasks are not verified planner records.
- No WebSocket push. Polling a single version counter is enough at one user. Section
  7.4 says how to upgrade later.
- No multi-user. One owner. No `owner` field.
- The initial implementation slice is undecided. Week, Month, and Year belong in
  the proposed product design; the release sequence will follow the product review.

## 2. Domain

Nine entities. Identifiers are ULIDs unless stated. Timestamps are ISO 8601 UTC strings.
Week ids are ISO weeks: `2026-W37`. Month ids: `2026-09`. Dates: `2026-09-08`.

| Entity | Fields | Notes |
|---|---|---|
| `FocusArea` | `focus_area_id`, `name`, `color`, `sort_order`, `archived` | User-defined names and count. Renaming preserves identity and linked records. Whether areas appear as rows is an open layout decision. |
| `Routine` | `routine_id`, `focus_area_id?`, `weekday` (0=Mon..6=Sun), `text`, `active`, `starts_on?`, `ends_on?` | Recurring personal routines. No built-in filing season or academic calendar. Recurrence details are provisional. |
| `Deadline` | `deadline_id`, `focus_area_id`, `title`, `due_date`, `recurrence` (`none` / `monthly` / `quarterly` / `annual`), `status` (`open` / `done` / `moved`), `source` (`user` / `agent` / `seed`), `moved_from` (list of dates), `notes` | Year-level facts. Not week-scoped. |
| `WeekPlan` | `week_id`, `start_date`, `focus_text`, `status` (`draft` / `active` / `closed`), `created_by`, `created_at`, `updated_at` | One per week. |
| `DayEntry` | `entry_id` (sha8, see 3.3), `week_id`, `date`, `focus_area_id`, `text`, `done`, `deadline_id?`, `order` | A cell in the week grid. |
| `Goal` | `goal_id` (sha8), `week_id`, `text`, `done`, `focus_area_id?` | Sidebar checklist, max 5 per week enforced in the client. |
| `Todo` | `todo_id` (sha8), `week_id`, `text`, `done`, `focus_area_id?`, `deadline_id?`, `carried_from_week?`, `status` (`open` / `done` / `carried`) | Sidebar checklist, unlimited. |
| `Review` | `review_id`, `kind` (`weekly` / `monthly`), `period` (week id or month id), `wins`, `misses`, `carry_forward` (list of todo ids), `written_by`, `closed_at` | Reflection and next steps across all areas. No required financial metrics. |
| `Proposal` | `proposal_id`, `week_id`, `kind` (name of a write tool), `payload` (that tool's arguments), `rationale`, `status` (`proposed` / `accepted` / `rejected`), `proposed_by`, `created_at`, `resolved_at?` | An unapplied write. Accepting runs the payload through the same code path as the direct tool. |

Month and Year views are derived, never stored.

## 3. DynamoDB

### 3.1 Table

One new table, `PlannerTable`, declared in `infra/planner_table.py` next to
`infra/dynamo_db.py`, same shape: PAY_PER_REQUEST, `PK`/`SK` string keys, point-in-time
recovery on, and these indexes:

| Index | Keys | Purpose |
|---|---|---|
| `GSITYPE` | hash `TYPE` | "All rows of a kind." Exports, backfills, tests. Portfolio convention. |
| `GSI1` | hash `GSI1PK`, range `GSI1SK` | Date-ordered reads: month view, deadlines by due date. |
| `GSI2` | hash `GSI2PK`, range `GSI2SK` | Sparse "open items" index: open todos, open proposals, last review. |

Export `planner_table_name`. Infrastructure remains a proposal in this product
revision. Follow the repository's environment restrictions: production operations
are prohibited for agents; a live dev test or deployment requires an explicit user
request. Dev is shared, not disposable.

### 3.2 Key layout

Every item carries `TYPE`. Index attributes are written only when the row belongs in
that index (sparse). `<sha8>` = first 8 hex chars of sha256 of the normalized text
(lowercase, whitespace collapsed, trailing punctuation stripped).

```
PK                  SK                               TYPE        GSI1PK / GSI1SK                       GSI2PK / GSI2SK
CONFIG              FOCUS#<focus_area_id>            FOCUS_AREA
CONFIG              CLOCK                            CLOCK                                              (see 3.5)
RHYTHM              ROUTINE#<weekday>#<ulid>          ROUTINE
DEADLINE#<ulid>     DEADLINE                         DEADLINE    DUE#<status> / <due_date>#<ulid>
WEEK#<week_id>      PLAN                             WEEK_PLAN
WEEK#<week_id>      DAY#<date>#<focus>#<sha8>        DAY_ENTRY   MONTH#<yyyy-mm> / <date>#<focus>#<sha8>
WEEK#<week_id>      GOAL#<sha8>                      GOAL
WEEK#<week_id>      TODO#<sha8>                      TODO                                              OPEN#TODO / <week_id>#<sha8>      (only while status=open)
WEEK#<week_id>      REVIEW                           REVIEW                                            LAST#REVIEW / <closed_at>
WEEK#<week_id>      PROPOSAL#<ulid>                  PROPOSAL                                          OPEN#PROPOSAL / <created_at>#<ulid> (only while status=proposed)
MONTH#<yyyy-mm>     REVIEW                           REVIEW                                            LAST#REVIEW / <closed_at>
```

`DUE#<status>` is one of `DUE#open`, `DUE#done`, `DUE#moved`. Moving a deadline keeps
`DUE#open` and rewrites `GSI1SK` with the new date.

### 3.3 Why these choices

- **A week is one partition.** `get_week` is one Query on `PK = WEEK#<id>` and returns
  plan, entries, goals, todos, review, and proposals together.
- **Deadlines have their own partition** because they move between weeks and are
  year-level facts. Week views join them by querying `GSI1` for the week's date range.
- **Content-hashed ids for entries, goals, todos** make `plan_week` and `add_todo`
  idempotent for free: the same text in the same week is the same key. Editing text
  is delete-plus-put of a new key, done inside `upsert_day_entry`.
- **Sparse GSI2** means "what is open" never scans closed history. Marking a todo done
  removes `GSI2PK`/`GSI2SK` in the same update.

### 3.4 Write semantics

| Operation | Mechanism |
|---|---|
| Create WeekPlan | `put_item` with `attribute_not_exists(PK)`. On failure return the existing plan. |
| Upsert entry / goal / todo | `put_item` keyed by sha8. Because the key is content-derived, re-putting identical content is a no-op by construction. Overwrites of the same key are allowed (they carry the same text). |
| Set done | `update_item` with `attribute_exists(PK)`; for todos also `REMOVE GSI2PK, GSI2SK` and `SET status = done`. |
| Carry todos forward | One `TransactWriteItems`: put the new week's todo (with `carried_from_week`), update the old one to `status = carried` and remove its GSI2 keys, bump clock. |
| Close week | One `TransactWriteItems`: put REVIEW, update PLAN `status = closed`, bump clock. Re-close overwrites REVIEW in place. Carry-forward runs first as its own transaction. |
| Move deadline | `update_item`: `SET due_date, GSI1SK, moved_from = list_append(...)`, condition `attribute_exists(PK)`. |
| Accept proposal | Apply the payload through the direct write path, then `update_item` proposal `status = accepted, resolved_at`, remove GSI2 keys. If applying fails, the proposal stays `proposed`. |
| Reject proposal | `update_item` `status = rejected`, remove GSI2 keys. |

DynamoDB transactions cap at 100 items; carry-forward batches todos in groups of 90.

### 3.5 The clock

`PK = CONFIG, SK = CLOCK` holds `version` (number) and `updated_at`. **Every write path
increments it**, either as an extra `Update` inside the transaction or as a separate
`update_item` with `ADD version :one` immediately after a single-item write. The front
end polls this one item. A write that forgets the clock is invisible to the page.

The client exposes `bump_clock()` and every mixin write method calls it. Add a unit
test that monkeypatches the low-level client and asserts each public write method
results in exactly one clock bump.

## 4. Package `planner/`

```
planner/
  pyproject.toml            hatchling; deps: boto3; extras [test]: pytest, moto, pytest-xdist; [lint]: black, isort, pylint, mypy
  planner/
    __init__.py
    constants.py            Domain status enums; focus-area names are user data
    ids.py                  Identifier and calendar helpers; identity strategy needs revision
    entities/
      __init__.py, util.py (copy only the validators you need from receipt_dynamo.entities.util)
      focus_area.py routine.py deadline.py week_plan.py day_entry.py goal.py todo.py review.py proposal.py
    data/
      __init__.py
      client.py             class PlannerClient(_FocusArea, _Routine, _Deadline, _Week, _Review, _Proposal, _Clock)
      _clock.py _focus_area.py _routine.py _deadline.py _week.py _review.py _proposal.py
      views.py              get_week_view(), get_month_view(), get_year_view(), get_status_view()  (pure functions over client reads)
      recurrence.py         materialize(deadline, until) -> list[Deadline]
    service.py              the tool-level operations shared by MCP + API: plan_week, close_week, close_month, add_todo, ... each with mode="apply"|"propose"
  seed/
    example_week.json       fictional, optional fixture; section 6
  tests/
    unit/                   entity round-trips, ids, recurrence, views over fixtures, clock-bump test
    integration/            moto: one test per access pattern in 3.6, one per write in 3.4, transaction atomicity
```

Entities follow `ReceiptSection` exactly: `@dataclass(eq=True, unsafe_hash=False)`,
`REQUIRED_KEYS`, validation in `__post_init__`, a `key` property, `to_item()` emitting
optionals only when set, `from_item()` parsing the SK, a module-level
`item_to_<entity>()`. Index attributes are computed in `to_item()`, never stored on the
dataclass.

`PlannerClient(table_name, region="us-east-1")` mirrors `DynamoClient`. Constructor
takes the table name explicitly. No config loading inside the package.

### 3.6 Access patterns the client must serve in one call each

| Method | Query |
|---|---|
| `get_week_items(week_id)` | `PK = WEEK#<id>` |
| `list_deadlines_between(start, end, status="open")` | `GSI1PK = DUE#open`, `GSI1SK BETWEEN start AND end~` |
| `list_month_entries(month_id)` | `GSI1PK = MONTH#<id>` |
| `list_open_todos()` | `GSI2PK = OPEN#TODO` |
| `list_open_proposals()` | `GSI2PK = OPEN#PROPOSAL` |
| `get_last_review()` | `GSI2PK = LAST#REVIEW`, `ScanIndexForward=False`, `Limit=1` |
| `list_routines()` | `PK = RHYTHM`, `SK begins_with ROUTINE#` |
| `list_focus_areas()` | `PK = CONFIG`, `SK begins_with FOCUS#` |
| `get_clock()` | `GetItem CONFIG / CLOCK` |

## 5. MCP server

File: `scripts/planner_mcp_server.py`. Stdio, official `mcp` SDK 1.x (pin `mcp<2`,
see the repo's known 2.x import failure). Module-level `TOOLS` list, one `call_tool`
dispatcher that maps names to `planner.service` functions. Under 400 lines; all logic
lives in the package.

Startup: read `PLANNER_ENV`. Resolve the table name from the Pulumi stack output
`planner_table_name` the same way `scripts/receipt_mcp_server.py` resolves its table
(`receipt_dynamo.data._pulumi.load_env`), or from `PLANNER_TABLE` if set. Exit 2 with a
one-line message if neither resolves.

### 5.1 Tools

Read tools return JSON. All responses include `{"env": ..., "table": ..., "clock": <version>}`.

| Tool | Args | Returns |
|---|---|---|
| `get_status` | | current week focus, done/total counts per row, next 3 open deadlines, open proposals count, last review period, clock |
| `get_week` | `week_id?` (default current) | full week view: plan, rows × days, weekend, goals, todos, deadlines due in range, proposals |
| `get_month` | `month_id` | per-day entry counts and deadlines, review if closed |
| `get_year` | `year` | deadlines by month |
| `list_due` | `days=30`, `focus_area?` | open deadlines |
| `list_open_todos` | `focus_area?` | open todos across weeks, with `carried_from_week` |
| `get_rhythm` | | routines, including active date ranges |
| `get_review` | `period` | review or null |
| `list_proposals` | `status="proposed"` | proposals |

Write tools. Every one takes `mode` (`"apply"` default, or `"propose"`) and
`rationale?`. In `propose` mode the tool stores a Proposal and applies nothing.

| Tool | Args | Idempotency |
|---|---|---|
| `plan_week` | `week_id?`, `from_rhythm=true`, `carry_todos=true` | create-if-absent; re-run seeds only missing entries and carries only still-open todos |
| `set_focus` | `week_id`, `text` | overwrite |
| `add_todo` | `week_id`, `text`, `focus_area?`, `deadline_id?` | sha8 key |
| `add_goal` | `week_id`, `text`, `focus_area?` | sha8 key; reject the 6th goal |
| `set_done` | `kind` (`todo` / `goal` / `entry` / `deadline`), `id`, `week_id?`, `done=true` | update in place |
| `upsert_day_entry` | `week_id`, `date`, `focus_area`, `text`, `done?`, `replace_id?` | sha8 key; `replace_id` deletes the old entry in the same transaction |
| `upsert_deadline` | `title`, `due_date`, `focus_area`, `recurrence?`, `deadline_id?`, `notes?` | put when new; update when id given |
| `move_deadline` | `deadline_id`, `new_due_date` | appends to `moved_from` |
| `close_week` | `week_id`, `wins`, `misses`, `numbers?` | re-close overwrites |
| `close_month` | `month_id`, `wins`, `misses`, `numbers?` | re-close overwrites |
| `resolve_proposal` | `proposal_id`, `decision` (`accept` / `reject`) | accept applies through the direct path; second call is a no-op returning current status |

Candidate `plan_week` inputs are active routines, upcoming deadlines, unfinished
work, and Tyler's stated priorities. Routine applicability uses the target dates,
not the date when the agent happens to run. Exactly what the agent schedules,
carries, or proposes is an open product decision; copying all open work into next
week is not an approved default.

### 5.2 Registration

During implementation, document optional local-agent registration using the repo's
Python 3.13 and an explicitly configured permitted environment. Do not change user
client configuration as part of this spec revision or register a production target.

## 6. Example and initial data

A new planner can start empty. Taxes, budget, and savings can be examples or initial
user-chosen areas; they are not required rows or default commitments.

Prototype and automated-test fixtures should use clearly fictional, varied content:
a project milestone, a household errand, a personal appointment, a recurring task,
and an unscheduled idea. Include an empty day and unfinished work so the prototype
shows realistic planning states, not just a fully populated grid.

Do not ship the earlier financial amounts, personal deadlines, appointments, or
private task list in a repository fixture or seed script. If Tyler later wants to
import real work, that is a separate explicit action, and uncertain dates must stay
uncertain instead of being assigned a plausible day.

The old requirement to seed a dated finance-specific week is removed. A future seed
or fixture loader must be repeatable without duplicating records or resetting work
that has been completed or edited.

## 7. HTTP API for the site

### 7.1 Where it mounts

On the existing auth-gateway HTTP API (`McpAuthGateway`), not on `api_gateway.py`.
Reasons: it already has the Cognito JWT authorizer and per-route scopes, and the
planner must not be anonymous. Add:

- Resource-server scope `planner` ("Use planner tools and API") alongside `receipt`
  and `glyph`. Add it to the interactive client's allowed scopes.
- Routes, all with `authorization_scopes = ["portfolio-mcp/planner"]`:

| Route | Handler | Response |
|---|---|---|
| `GET /planner/api/clock` | one `GetItem` | `{"version": n, "updated_at": ts}` |
| `GET /planner/api/week/{week_id}` | `views.get_week_view` | the same JSON `get_week` returns |
| `GET /planner/api/status` | `views.get_status_view` | same as `get_status` |
| `POST /planner/api/proposals/{proposal_id}` | body `{"decision": "accept" \| "reject"}` | resolved proposal |

Generalize the `for route_name in ("receipt", "glyph")` loops in
`infra/mcp_auth_gateway.py` so a third server and a non-MCP route family can register
without copy-paste. Keep existing resource names and URNs unchanged (Pulumi will
otherwise replace the live receipt and glyph routes).

CORS: the gateway already sets `cors_configuration`. Add the site origins
(`https://tylernorlund.com`, `https://www.tylernorlund.com`, `http://localhost:3000`)
and `Authorization` to allowed headers.

### 7.2 Handler

One zip Lambda, `infra/planner_api/lambdas/api.py`, stdlib + boto3, routing on
`rawPath`. It imports `planner` via a Lambda layer built the same way `dynamo_layer` is
built in `infra/components/lambda_layer.py`, or vendors the package into the zip;
pick whichever `infra/components/lambda_layer.py` makes easier and say which. IAM:
`Query`, `GetItem`, `UpdateItem`, `TransactWriteItems` on the table and its three
indexes. Environment: `PLANNER_TABLE`, `PLANNER_ENV`.

### 7.3 Contract for live updates

The page polls `GET /planner/api/clock` every 3 seconds while the tab is visible
(`document.visibilityState`), every 30 seconds when hidden. When `version` changes,
it fetches `/week/{current}` once. Nothing else polls. Cost: one GetItem every 3 s
for one user, which rounds to zero.

### 7.4 Later upgrade, not in this build

DynamoDB Streams on `PlannerTable` → Lambda → API Gateway WebSocket `postToConnection`.
The page contract stays the same (a version number arrives; the page refetches), so
this swaps in without changing the week view.

## 8. Site page

`portfolio/pages/planner.tsx`, components under `portfolio/components/planner/`.

### 8.1 Auth

Cognito hosted UI, authorization-code with PKCE, using the existing public client
(`mcp_oauth_interactive_client_id`) and scope `portfolio-mcp/planner openid`. Add
`https://tylernorlund.com/planner` and `http://localhost:3000/planner` to
`portfolio:mcpOAuthCallbackUrls` in both stacks. Tokens live in `sessionStorage`;
refresh via the refresh token; on any 401, clear and show the sign-in button.
Implement PKCE by hand (about 60 lines: `crypto.subtle` SHA-256, base64url) instead of
adding Amplify.

Bake the two URLs the page needs at build time as `NEXT_PUBLIC_PLANNER_API_URL` and
`NEXT_PUBLIC_PLANNER_AUTH_ISSUER` plus `NEXT_PUBLIC_PLANNER_CLIENT_ID`, wired in
`.github/workflows/main.yml` next to the existing `NEXT_PUBLIC_GA_MEASUREMENT_ID`
line, read from `pulumi stack output`.

The page must not be linked from the site nav. It is reachable by URL only.

### 8.2 Layout

Use the paper photos as the primary visual reference. The HTML concept is useful
background, but its dense grid and fixed finance rows are not the target contract.

Proposed visual requirements for discussion:

- Clear week/date heading, generous open space, subtle shaded sections, and thin
  ruled lines. Keep database versions and tool names out of the main planner view.
- Mon-Fri planning space with day contents organized according to the pending
  weekly-layout decision. No subject labels or mandatory number of rows.
- Preserve the recognizable sidebar: This Week's Focus, Weekly Goals, Things To
  Do, and smaller Saturday/Sunday sections, as shown in the right-page photo.
- Weekend content can carry any optional focus area; do not force it into OTHER.
- Show completion and due dates clearly. A planned work date and an actual deadline
  must be distinguishable.
- Present agent proposals as readable changes with a reason, not raw tool payloads.
  Direct-editing controls depend on the pending interaction decision.
- Provide Week, Month, and Year navigation in the design. Define the implementation
  order after reviewing the prototype.
- Design a narrow-screen view explicitly. A wide table with horizontal scrolling
  alone does not establish a usable mobile layout.
- Support the site's light and dark themes while retaining the paper's hierarchy.

The initial prototype must show varied fictional work, an empty day, completed and
unfinished tasks, a moved item, and a proposed change. Compare desktop and mobile
renders with the photos before finalizing layout acceptance criteria.

### 8.3 Fetch layer

`portfolio/services/api/planner.ts` with `getClock`, `getWeek`, `getStatus`,
`resolveProposal`, all attaching the bearer token. A `usePlannerWeek(weekId)` hook
owns the polling loop from 7.3 and exposes `{week, loading, error, version}`.

### 8.4 Tests

Jest tests for the hook (fake timers: version change triggers exactly one refetch;
hidden tab slows the interval) and a render test of the grid with a fixture week.

## 9. Infrastructure and CI: retained direction

Keep the planner separate from the receipt packages. Candidate components are a
`planner` Python package, a planner table, a shared service used by MCP and HTTP,
and a page with components under `portfolio/components/planner/`.

Reuse established authentication and packaging conventions where they fit. The
existing auth gateway already uses a route list that includes an optional ATS
server; do not implement the earlier instruction to generalize nonexistent
receipt/glyph-only loops. Keep existing resources stable.

The current site workflow builds the frontend before deploying infrastructure.
Any build-time planner outputs need a deliberate first-deployment strategy. The
issuer, authorization endpoint, and token endpoint are separate configuration
concepts; the final auth contract must specify all required values.

This revision authorizes no deployment. Follow AGENTS.md: agents may not operate
production or trigger its deployment. Live dev work requires an explicit request,
AWS account verification, commands pinned to `tnorlund/portfolio/dev`, and a preview
that is reviewed for unrelated replacements or deletes. Never interrupt a running
shared-stack update. Ordinary package and frontend verification remains local.

## 10. Agent access and runtime

MCP tools are an interface an agent can call. They do not by themselves decide when
to run, how to prioritize work, or how to notify Tyler.

After the initiative decision, specify the actual planning loop: trigger, inputs,
allowed changes, stop conditions, and what Tyler sees. A scheduled or hosted agent
is a separate component with an explicit operating policy, not a capability assumed
from using DynamoDB. No automation is being created during this revision.

The initial location of conversation and whether remote MCP belongs in the first
release remain open. Both local and remote clients should use the same planner
service and identity/authorization rules.

## 11. Proposed delivery sequence

The earlier four-PR sequence is withdrawn pending the product revision. It placed
backend construction before resolving the user's planning workflow and layout.

1. Resolve the product choices and describe a concrete planning/replanning example.
2. Build a local prototype with fictional data and inspect desktop and mobile views.
3. Revise the data model, command contracts, and acceptance cases to match that UI
   and the chosen agent behavior.
4. Implement persistence and the shared service, then connect the interface and MCP.
5. Verify a real local end-to-end change and refresh. Plan any live dev work
   separately under the repository's explicit deployment rules.

Use small reviewable feature branches. No implementation, PR creation, merge, or
release is part of the current collaborative spec revision.

## 12. Decisions and assumptions

The product-direction table at the top is the current decision record. The original
list of twelve assumptions is withdrawn where it fixed categories, excluded direct
editing, forced weekends into OTHER, or imported finance-specific behavior.

Retained engineering proposals, subject to the revised product design:

- One private owner initially.
- Use America/Los_Angeles for local dates and the current week; persist actual
  event timestamps in UTC.
- Keep planner storage and services separate from the receipt packages.
- Validate both direct agent writes and accepted proposals through the same service.
- Refresh the rendered view after changes; polling is a candidate implementation.
- Keep real financial and other external systems independent of planner records.

Still to define after the first three product decisions:

- Whether conversation lives inside the planner, in external agent clients, or both.
- The meaning of tasks, scheduled work, deadlines, and appointments, including what
  happens when the same task appears in multiple views.
- What carrying unfinished work forward does, and which changes need a proposal.
- Priorities, available time, fixed commitments, and other planning preferences.
- Month/Year behavior, mobile layout, and the first implementation slice.
- Whether reminders and scheduled agent runs belong in the first release.

Do not treat this revision as an instruction to schedule agents or deploy services.

## 13. Engineering issues to resolve before implementation

The earlier domain, key-layout, tool, and HTTP sections above are retained as draft
material. They require a coherent rewrite after the product choices are resolved;
they are not acceptance-ready instructions. Specifically:

- Replace text-derived identity with stable identity. Repeating a create request
  must not reset completion, and editing text must not break references. Define
  request idempotency separately from task identity.
- Define one authoritative task state across daily placement, the weekly list,
  deadlines, and carry-forward. Resolve whether DayEntry is a task placement, a
  note, or both before freezing the schema.
- Persist each change and its refresh version together. Handle a stale read or
  failed refetch so seeing a new version does not leave the screen permanently old.
- Make proposal application and resolution atomic or durably idempotent. A retry
  after a partial failure must not apply the same change twice. Detect stale
  proposals before overwriting subsequent edits.
- Count actual write actions when batching: each carried todo in the earlier
  design needs two actions, plus the clock update. The old batch of 90 todos does
  not fit its stated 100-action limit.
- Specify completion/reopening, edit/move/archive, recurring-instance exceptions,
  routine management, and proposal conflicts as supported operations.
- Revisit API routes after deciding direct editing and additional calendar views.
  Keep validation and behavior shared with agent commands.
- Verify retry, interrupted-write, conflicting-edit, repeated-planning, and
  cross-view consistency scenarios. Do not use a count of clock bumps or raw
  byte-identical records as substitutes for user-visible correctness.

Keep these issues separate from the product conversation. Resolve the user-facing
behavior first, then update the engineering contract and tests around that behavior.
