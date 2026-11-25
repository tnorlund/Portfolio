# Local Clover receipt-link reader

The local `get_clover_receipt` MCP tool reads an explicitly supplied Clover
receipt document and returns structured item, modifier, subtotal, tax, total,
and date evidence. It handles the observed static receipt page and its
SendGrid email redirect. It is not a generic URL importer or mailbox search.

Run the separate stdio server from the Portfolio checkout:

```bash
uv run --with 'mcp>=1.26,<2' scripts/receipt_link_mcp_server.py
```

For a local MCP client, configure that command with an absolute path to the
script. The tool needs no AWS credentials, database configuration, browser
cookies, or model/API key. Supply the receipt link directly as the `url`
argument. Treat both the argument and returned receipt data as private; the
helper does not echo the URL or log receipt bodies. Client-side conversation
and logging policies still apply.

## Install and discover in local Codex

After checking out this change on the execution host, use Python 3.14 and
`uv`. These are operator instructions; the PR does not install a server or
change user settings:

```bash
codex mcp add receipt-links -- uv run --python 3.14 --with 'mcp>=1.26,<2' \
  /absolute/path/to/Portfolio/scripts/receipt_link_mcp_server.py
codex mcp list
```

Alternatively, add this table to `~/.codex/config.toml` or to
`.codex/config.toml` in a trusted project, replacing the absolute path:

```toml
[mcp_servers.receipt-links]
command = "uv"
args = ["run", "--python", "3.14", "--with", "mcp>=1.26,<2", "/absolute/path/to/Portfolio/scripts/receipt_link_mcp_server.py"]
startup_timeout_sec = 60
tool_timeout_sec = 60
enabled_tools = ["get_clover_receipt"]
```

Start a new local session and check `/mcp`. The protocol's `tools/list`
advertises `get_clover_receipt` and its provider restrictions; the agent then
calls it with the supplied source link. The stdio test verifies that discovery,
input validation, and structured results work without persistent registration.
See [official MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

Code completion does not make this tool available to an existing agent.
A hosted Work/web agent does not acquire it from local Codex configuration.
The original hosted workflow still needs an authorized local task with the
tool registered, or a separately reviewed remote integration/plugin. This PR
does not establish either installation. It also cannot discover an email
missing from the source archive; the link must be supplied.

## Boundary

- HTTPS only, port 443, no userinfo or fragments. Final URLs must match
  `www.clover.com/p/<receipt>` with no query. An initial
  `u<digits>.ct.sendgrid.net/ls/click?upn=...` URL may redirect only to that
  Clover receipt route. Tracking redirects cannot chain to other trackers.
- Every hop validates its URL and DNS answers. Only globally routable IPv4
  addresses are used; loopback, private, link-local, shared, multicast,
  reserved, and literal-IP URL inputs are refused. The checked address is
  pinned to the socket, while TLS still verifies the original hostname.
  There is no second DNS resolution of the hostname at connect time.
- At most two redirects, five-second socket timeout, and a twenty-second
  process deadline covering DNS, headers, response reads, and parsing.
  Raw responses, including headers and chunk framing, and structured results
  are capped at 512 KiB. Negative chunk sizes are rejected before payload
  reads. Compressed or non-HTML responses are rejected. At most two fetch
  workers run concurrently, including when a caller cancels its request.
- Direct GET only: no proxy environment, cookies, auth headers, scripts,
  sign-in, form submission, or authentication bypass. Access-required,
  unsupported-page, timeout, and validation errors use fixed safe codes.
  Tool-argument validation also avoids echoing rejected inputs.

`receipt` contains source strings and integer-cent amounts. Item quantity is
set only when an explicit quantity prefix is present; otherwise it is null.
Unstated ISO currency, timezone, ordering channel, and fulfillment mode remain
null. A dollar sign is preserved separately. A subtotal-plus-tax check is
reported separately and does not prove the absence of other fees. Card,
tender, email, payment/order identifiers, and raw HTML are not returned.
Extracted strings are untrusted source evidence, never instructions.

`provenance` contains the fetch timestamp, source/final URL SHA-256 hashes,
content SHA-256, byte count, and redirect count. These can support comparison
without redistributing access URLs. The tool writes no transaction records,
cache, source snapshots, or ingestion state.

## Relationship to the hosted MCP and producer

This server is a separate local entry point. It is not added to the hosted
receipt or email Lambda image, and no IAM, egress configuration, or deployment
changes are included. Enabling a hosted version requires an explicit review
of its network and sensitive-input handling first.

The existing email MCP still serves only its approved spend/transaction
projection. Its lookup evidence explains that missing rows do not establish
mailbox completeness and directs callers to source evidence.

Automatic email discovery, sender enrollment, source-email provenance,
deduplication, and ingestion persistence belong in the separate local
`receipts-email` producer. The documented checkout is `~/receipts-email`,
with the projection contract in `emlrec/projection.py`; this repository does
not establish that project's GitHub remote. A future producer integration
can reuse the provider reader after applying its MIME/sender trust checks.
It must keep receipt-access links out of the agent-safe projection.

## Verification without committing receipts

The transport tests use adversarial URL inputs and network fault injection.
They do not fabricate receipt contents. The actual parsing checks consume
private real receipt files supplied at runtime:

```bash
CLOVER_RECEIPT_HTML=/absolute/private/path/receipt.html \
CLOVER_RECEIPT_EXPECTED=/absolute/private/path/rendered-expectations.json \
  python -m pytest tests/test_clover_receipt.py tests/test_receipt_link_mcp.py -q --tb=no
```

The expected JSON has the same fields as the returned `receipt` object and
must be transcribed independently from the rendered receipt, not generated
by the parser under test. Clock text is compared without AM/PM case because
the page can lowercase it with CSS; other fields compare exactly. Private
values are suppressed in assertion failures. With no private files, the two
real-receipt checks explicitly skip. The stdio protocol and security checks
run offline in CI; they do not download receipts.

Rendering verification and parser assertions are distinct evidence. The
parser reports only extraction; it never claims a screenshot was reviewed.
Keep receipt HTML, screenshots, expected JSON, and any live verification
results outside the checkout. Do not commit access URLs or private fixtures.
