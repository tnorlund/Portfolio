---
name: qa-agent-eval
description: >-
  Evaluate the receipt QA agent's 32 marquee questions end to end: deploy to dev,
  run the step function, verify the viz cache, grade every question with parallel
  agents, and update SCORECARD.md. Use when iterating on
  receipt_agent/agents/question_answering/ or when asked to re-grade the QA agent.
---

# QA agent evaluation loop

Code change → Pulumi deploy (dev) → Step Function (32 questions) → S3/LangSmith
→ dev API `/qa/visualization` → 8 parallel grading agents → `q00.md`–`q31.md`
+ `SCORECARD.md`.

## Key files

- `receipt_agent/receipt_agent/agents/question_answering/graph.py` plan/agent/tools/shape/synthesize nodes and prompts.
- `receipt_agent/receipt_agent/agents/question_answering/state.py` state schema.
- `receipt_agent/receipt_agent/agents/question_answering/tools/search.py` tool implementations, state_holder population.
- `receipt_agent/receipt_agent/agents/question_answering/tools/__init__.py` agent system prompt.
- `infra/qa_agent_step_functions/infrastructure.py` step function infra.
- `infra/qa_agent_step_functions/lambdas/run_question.py` run-question Lambda.
- `infra/routes/qa_viz_cache/lambdas/index.py` viz cache API handler.
- `SCORECARD.md` cumulative grades. Per-question evals live in git worktrees
  `qa-eval-q{NN}/qa_evaluation/q{NN}.md` on branches `chore/qa-eval-q{NN}`.

## 1. Deploy to dev (only when the user asked for a live dev run)

```bash
cd infra
pulumi preview --stack tnorlund/portfolio/dev
pulumi up --stack tnorlund/portfolio/dev --yes
```

CodeBuild rebuilds the QA Lambda image (`receipt_agent`, `receipt_dynamo`,
`receipt_chroma`) when sources changed, ~5 min.

## 2. Run the step function

```bash
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-east-1:<account-id>:stateMachine:qa-agent-dev" \
  --input '{"langsmith_project": "qa-eval-<descriptive-name>"}' \
  --region us-east-1

aws stepfunctions describe-execution --execution-arn "<execution-arn>" \
  --region us-east-1 \
  --query '{status: status, startDate: startDate, stopDate: stopDate}'
```

Runs all 32 questions, exports LangSmith traces, and builds the viz cache with an
EMR Spark job. ~20–25 min.

## 3. Verify the viz cache

```bash
curl -s "https://dev-api.tylernorlund.com/qa/visualization" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d.get('metadata', {}), indent=2))
"
```

`cached_questions` must be 32 and `execution_id` must match your run.

Endpoints: `GET /qa/visualization` (metadata), `?index=N` (one question with full
trace), `?all=true` (all 32). Trace items have `type` in `plan`, `agent`,
`tools`, `shape`, `synthesize`.

## 4. Grade with 8 parallel agents

Spawn 8 background general-purpose subagents, each owning 4 consecutive
questions (Q0–Q3 … Q28–Q31). Each agent:

1. Fetches `curl -s "https://dev-api.tylernorlund.com/qa/visualization?index=N"`.
2. Parses the `trace` array: plan, tool calls, agent reasoning, shape, final answer.
3. Reads the existing `qa-eval-q{NN}/qa_evaluation/q{NN}.md`.
4. Verifies correctness with the MCP receipt tools (`search_receipts`,
   `search_product_lines`, `get_receipt_summaries`, `get_receipt`, ...).
5. Rewrites the evaluation (format in `references/evaluation-format.md`).
6. Reports grades back.

Grading scale: A correct with evidence matching MCP; B mostly correct, minor
issues; C partially correct or missing context; D significant errors; F wrong or
critical data loss. If the agent is right but the synthesizer contradicts it,
that is a D or F (synthesis override bug).

## 5. Update `SCORECARD.md`

Add a Scores column, a Run Log row (commit, date, LangSmith project), a Summary
row with the grade distribution, and an Iteration History entry explaining what
changed and why. Commit `SCORECARD.md` on the evaluation branch; the `q*.md`
files are committed in their own worktrees.
