# Receipt validation through Anthropic BYOK

Dev receipt processing selects `anthropic/claude-haiku-5.5` through
`portfolio:RECEIPT_OPENROUTER_MODEL`. The existing OpenRouter credential and
endpoint remain in use. OpenAI still provides embeddings. Haiku 5.5 is the
newest Haiku in the Anthropic and OpenRouter catalogs as of 2026-10-14. It
supports tools and structured outputs but not temperature; the factory omits
temperature for this exact model while preserving other models’ behavior.

Selecting this Haiku model automatically uses the Anthropic-only adapter.
There is no additional provider setting. It applies `provider.only=["anthropic"]`,
disables fallback, and requires parameter support after LangChain merges request
options. Bound or per-call options cannot remove or broaden that route. Other
models retain their existing client behavior.

The shared receipt-processor default is also Haiku. Dev has an explicit Haiku
override; the checked-in production configuration has no receipt-model override,
so a later production deployment would pick up the new default. This change is
deployed only to dev; production requires its separate authorization.

Haiku requests explicitly disable thinking because ChatOpenAI does
not preserve OpenRouter thinking metadata across tool results. The Haiku
endpoint currently rejects named and `required` tool choices. For
one identified function, the adapter requests its arguments with the original
JSON schema and Pydantic validation, retains the conversation’s tool
definitions with tool selection disabled, then exposes the result as a tool
call with usage metadata. Confidence/source maps enumerate the four supported
place fields, preserving nonempty values through the provider’s schema
conversion. This keeps the merchant final-submission contract; ambiguous choices fail locally.

Provider restriction alone does not guarantee BYOK billing. Before ingestion,
verify that the existing Anthropic BYOK key applies to the selected model and
OpenRouter API key, and that shared Anthropic capacity fallback is disabled.
Do not change credentials or account settings as part of this deployment.
A synthetic structured-output request must report Anthropic and
`usage.is_byok=true` before receipt data is processed.

## Deployment and rollback

Keep receipt uploads paused. Save the current dev configuration and image
digest. Preview against `tnorlund/portfolio/dev`, using only the receipt
processor build/configuration targets; inspect every proposed resource change.
Stop if the preview includes unrelated resources, IAM changes, or AWS resource
replacements. Replacing the receipt processor’s local upload-context and
pipeline-trigger commands is expected when rebuilding its image. Use exact
resource URNs for these build commands and the receipt Lambda.

The Haiku adapter requires the updated factory in the container image.
Deploy and verify that image **before** activating the new model. A model-only
change on the old image does not enforce routing or provide compatibility. Use
a temporary dev config file retaining the previous model for the code-build
phase, then preview and apply the final model setting.
Wait for CodeBuild and Lambda updates to finish; an async build trigger alone
is not verification. Do not change the production model configuration.

After the final scoped `pulumi up --stack tnorlund/portfolio/dev`, verify the
deployed image and environment, then test the real structured-output path and
tool-call path with synthetic input. Confirm the returned provider and BYOK
usage before resuming journaled uploads. No test should contain private receipt
data until that routing check passes.

For rollback, restore the saved receipt model, preview the same dev targets,
and apply the configuration. Other models use the original client behavior. Preserve
the previous image digest for a separately reviewed image rollback if needed.

OpenRouter documents [provider restrictions](https://openrouter.ai/docs/guides/routing/provider-selection)
and the separate [BYOK fallback controls](https://openrouter.ai/docs/guides/overview/auth/byok).

Model references: [Anthropic Haiku 5.5](https://www.anthropic.com/claude-haiku-5-5)
and the [OpenRouter model catalog](https://openrouter.ai/api/v1/models).
