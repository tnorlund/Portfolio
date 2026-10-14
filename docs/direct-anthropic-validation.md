# Direct Anthropic receipt-label validation (opt-in)

`LLMBatchValidator` supports the direct Anthropic Messages API while retaining
the existing label decision contract: index, decision, label, confidence, and
brief reasoning. Only receipt label validation changes. Merchant resolution,
QA, OCR, and direct OpenAI `text-embedding-3-small` embeddings are unaffected.
There is no automatic provider fallback. This change does not enable the
provider, create credentials, deploy, or authorize receipt processing.

This is an optional direct transport. An existing Anthropic BYOK key in
OpenRouter may already bill the credit-bearing Claude Console organization.
Verify that key's organization, routing filters, and shared-capacity fallback
before deciding whether a direct-provider switch or new credential is needed.

## Configuration and secure setup

| Environment / portfolio config key | Purpose |
| --- | --- |
| `RECEIPT_VALIDATION_PROVIDER` | `anthropic` opts in; absent keeps existing `openrouter` behavior. Unknown values fail. |
| `RECEIPT_ANTHROPIC_MODEL` | Required explicit Claude API model ID; no default or cross-provider model alias. |
| `ANTHROPIC_API_KEY` | Required direct API key. Pulumi uses `require_secret` and preserves the secret Output. |
| `RECEIPT_ANTHROPIC_MAX_TOKENS` | Output limit, default 8192, allowed 1–16384. Includes thinking tokens. |

After owner approval at the time of setup:

1. In the [Claude Console](https://platform.claude.com/), select the intended
   credit-bearing organization/workspace and create a dedicated API key using
   its API Keys settings. A Claude application subscription or its OAuth token
   is not a substitute. Never paste a key in chat, source, a PR, or shell history.
2. For a local evaluation, inject the API key into only the evaluation process
   using a hidden terminal prompt or approved secret manager. Disable hosted
   tracing and leave `RECEIPT_TRACE_BUCKET` unset so artifacts stay local.
3. Persistent dev configuration is a separate approval step. The owner can use
   `pulumi config set --secret portfolio:ANTHROPIC_API_KEY --stack tnorlund/portfolio/dev`
   and enter the value at its hidden prompt. Set the nonsecret provider, model,
   and token limit keys on that same fully qualified dev stack after reviewing
   the exact planned settings. Do not include the secret in command arguments.
4. Review the dev infrastructure preview and obtain deployment authorization.
   This integration requires no new IAM grants, Secrets Manager secret, or
   billing/auto-reload changes. Production promotion remains a separate review.

The client fixes its endpoint to `https://api.anthropic.com`, ignores gateway
base-URL overrides, requires an API key, and rejects `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_CUSTOM_HEADERS`.
SDK credential/profile auto-discovery is bypassed by the explicit API key.
Other agents continue using their existing configuration; switching this
validator does not authorize running those other agent paths.

## Contract, retries, and provenance

The implementation uses `anthropic>=1.13.0,<2.0.0`, verified offline with 1.13.0
on Python 3.14, and the current `output_config.format` JSON-schema API. It omits
temperature/top-p and forced tools for compatibility with newer Claude models.
It requests the standard service tier. The API schema contains label types,
never receipt content. Receipt text is sent only in the user message.

Only an `end_turn` response with exactly one JSON text block, valid typed
decisions, and every input index covered exactly once can reach the apply step.
Refusals, truncation, malformed JSON, unknown labels, missing/duplicate indexes,
and API errors fail closed. Explicit uncertainty stays `NEEDS_REVIEW` even if
the suggested label differs. Existing alias normalization remains in place.

The SDK makes at most three attempts per validator call (two retries) for its
documented transient errors, with a 120-second timeout per attempt. It does not
retry authentication failures. After exhaustion, the async caller raises so the
existing SQS redrive/DLQ policy applies and the staged payload remains available.
The sync upload path leaves pending labels intact on failure, allowing embedding
work to complete without treating failed validation as accepted labels.

Existing persistence semantics are retained: corrected labels use their existing
keys and tolerate duplicate creates; a successful async consumer deletes the
staged payload, making later deliveries with a missing payload no-ops. This is
**at-least-once**, not exactly-once inference: a crash or failed payload cleanup
can cause another billed request, and partial writes may be replayed. The request
fingerprint is an audit identifier, not an Anthropic deduplication guarantee.

Successful decisions retain requested/returned model, direct provider, message
and request IDs, SHA-256 request fingerprint, token usage, stop reason, and elapsed
time in the existing reasoning audit field. Native trace output also carries
this metadata. A fingerprint includes model, prompt, schema, token limit, and
service tier. SDK error bodies are not copied into failure messages. Successful
response usage does not account for any uncertain billing after a lost response.

## Evaluation gate

Offline HTTP tests exercise the actual SDK and existing response conversion,
without live receipt transmission. Private receipt snapshots can be replayed
locally through a mock transport to check request construction and coverage;
that does not establish model accuracy or live latency. Keep these artifacts
outside the repository. Do not replace a real receipt acceptance run with the
small synthetic fixtures used for failure-path unit tests.

An initial proposed live evaluation is **three real receipts**, serially, with
SDK retries disabled, at most 20,000 counted input tokens per request and 8192
output tokens, using `claude-sonnet-5-5`. As checked on 2026-10-11, standard direct
pricing is $2 per million input tokens and $10 per million output tokens.
That gives an estimated ceiling of **$0.36576** for three successful requests
within those token limits; typical short label outputs should cost less.
Before starting, verify current pricing, use the token-count endpoint with the
exact messages and schema, allow headroom for estimation/structured-output prompt
overhead, and reject requests exceeding the approved budget. Approve a **$0.50
evaluation budget** separately if no existing budget covers this work. Do not
automatically expand the sample or retry a paid failure. No paid evaluation is
performed by the tests or this document.

For acceptance, compare every decision with an independently reviewed baseline,
report false validations/corrections and abstentions, and manually review any
disagreement against the real receipt. Existing model labels alone are not gold
truth. Report token usage, actual estimated cost, per-receipt latency, errors,
and the model provenance. Keep outputs local/dev and do not apply evaluation
decisions to DynamoDB. Credits must be available in the Claude API organization.
OpenRouter shared capacity and Bedrock do not use those credits. Anthropic BYOK
through OpenRouter bills the underlying provider account; combined with the
organization-wide credit policy, this suggests eligible BYOK requests can use
the credits. That is a cross-document inference, not verified account routing
or a vendor guarantee for this combination. OpenRouter fees are separate.

## Rollback

Pause the deferred validator consumer before changing a provider when queued
payloads exist; queued payloads use the consumer's runtime configuration.
Inspect and account for in-flight work, then keep payloads for a reviewed replay.
If reverting to the previous provider is explicitly authorized, restore the
recorded `RECEIPT_VALIDATION_PROVIDER=openrouter` and its prior model config in
a reviewed dev deployment. Do not use that rollback to resume a denied provider.
Without alternate-provider authorization, keep validation paused and pending
labels unresolved. Never delete the embedding vectors as part of this rollback.

Official references (verified 2026-10-11):

- [Structured outputs and stop reasons](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Sonnet 5.5 compatibility and pricing](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)
- [Python SDK, errors, and retries](https://platform.claude.com/docs/en/api/sdks/python)
- [Model IDs](https://platform.claude.com/docs/en/models/overview)
- [OpenRouter BYOK routing and billing](https://openrouter.ai/docs/guides/overview/auth/byok)
- [Claude subscriber API credits](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)
