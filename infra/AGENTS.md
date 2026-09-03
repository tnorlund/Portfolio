# infra/ (Pulumi, Python)

Deltas to the root `AGENTS.md`. The hard rules there apply with no exceptions:
prod is off limits, and dev deploys happen only when the user explicitly asks.

- Always pass the fully qualified stack and preview first:
  `pulumi preview --stack tnorlund/portfolio/dev`, then
  `pulumi up --stack tnorlund/portfolio/dev`. Run from `infra/` or with
  `pulumi -C infra`. Never rely on the currently selected stack.
- Before any `up`, confirm `aws sts get-caller-identity` returns account
  `681647709217` and read the preview: refuse deletes or replacements that are
  unrelated to the change, and never interrupt an in-progress update (the dev
  stack is shared).
- `__main__.py` is the entry point; reusable resources live in `components/`
  (`lambda_layer.py`, `codebuild_docker_image.py`, `route_lambda.py`,
  `http_api_route.py`, ...). Feature stacks have their own directories
  (`sagemaker_training/`, `coreml_export/`, `qa_agent_step_functions/`,
  `routes/`, `lambda_functions/`).
- Container images are rebuilt by CodeBuild when their source hash changes
  (`components/codebuild_docker_image.py`); a deploy that touches image sources
  takes several extra minutes. Lambda layers are built by `lambda_layer.py`,
  which resolves package paths from the repo root.
- Infra unit tests live next to the code as `components/test_*.py` and under
  `infra/tests/`; run them with `pytest infra` from the repo root. They must not
  need AWS credentials.
- `Pulumi.*.yaml` files hold stack config. Do not add secrets to them; use
  `pulumi config set --secret` on the dev stack when the user asks.
