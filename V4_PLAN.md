# Label Harmonizer V4 Plan

  ## Goals
  - Run financial and metadata sub-agents in parallel at the start (avoid sequential dependency).
  - Simplify financial discovery by folding table detection/analysis into the financial sub-agent
  itself.
  - Standardize financial outputs so evaluators and feedback are meaningful (for LangSmith).
  - Keep LangSmith tracing/versioning clean for side-by-side comparison with V3.

  ## Changes to implement
  1) Parallel kickoff
     - At harmonizer start, launch financial discovery (with built-in table parsing) and metadata sub-
  agent concurrently.
     - Merge outputs into state before any label sub-agent calls.

  2) Financial sub-agent simplification
     - Inline table detection: detect columns/line ranges inside the financial sub-agent.
     - Extract numeric candidates and perform math checks locally (sum line totals; subtotal + tax vs
  grand total; handle discounts/fees if present).

  3) Standardized financial output schema (tool result)
     - Emit fields: `grand_total`, `subtotal`, `tax_total`, `line_total_sum`, `discount_total`,
  `tip_total`, `currency` (optional), `math_tests` array with pass/fail, `financial_fields` dict keyed
  by type, `issues`/`reasoning`.
     - Always run math tests and report `total_tests`/`verified`.

  4) LangSmith/LLM usage
     - Keep Ollama Cloud via `create_ollama_llm(settings)`.
     - Tag runs with V4 metadata: set `config.metadata` to include `version: "v4"`, `workflow:
  "label_harmonizer_v4"`, and set `run_name`/`thread_id` to keep runs separate.
     - Ensure `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_PROJECT` set to a V4 project name for
  comparison.

  5) Evaluator alignment
     - Use the existing evaluator but rely on standardized financial outputs (structured totals and
  math_tests) rather than only parsing the final AI message.
     - Continue writing `financial_eval` feedback to runs for UI filtering/sorting.

  ## Rollout steps
  1) Port the current changes (line_items sub-agent + evaluator) into the clean repo (done via copy).
  2) Implement V4 graph changes (parallel financial + metadata, standardized financial tool output) in
  the clean repo.
  3) Run LangSmith-traced tests on a small batch, review feedback scores, then scale.
  4) Once the StepFunction run finishes, snapshot a LangSmith dataset from V3 runs and re-run eval on V4
  for apples-to-apples comparison.


What the evaluator revealed

  - The bottleneck is the financial tool output: validate_financial_consistency often returns
    total_tests=0 and lacks structured grand_total/subtotal/tax fields. Without math checks or
    normalized totals, scores stay low.
  - Final AI messages sometimes include totals, but scraping them is fragile; structured outputs are
    needed.
  - Many runs were pending/no-output; filtering to completed runs improved signal but still showed
    missing math/labels.
  - Currency isn’t the issue; math and required financial fields are.

  Big picture

  - The agent pipeline is over-complicated: table sub-agent + financial sub-agent handoff is brittle,
    causing missing math/fields.
  - To improve accuracy and evaluability, the financial sub-agent needs to own table parsing and emit
    standardized, structured results with executed math tests.
  - Clear run tagging (version/workflow) and consistent tracing are key for comparing V3 vs V4 in
    LangSmith.

  How to get better results

  1. Simplify financial discovery:
      - Inline table detection/column inference inside the financial sub-agent.
      - Extract numeric candidates and run math checks locally (sum line totals; subtotal + tax vs grand
        total; handle discounts/fees).
      - Emit structured outputs: grand_total, subtotal, tax_total, line_total_sum, discount_total,
        tip_total, math_tests (with pass/fail and counts), financial_fields dict, issues/reasoning.
  2. Parallelize early steps:
      - Run financial discovery and metadata sub-agent in parallel at the start; merge results into
        state before any labeling.
  3. Standardize tracing/versioning:
      - Tag runs with version: "v4" and workflow: "label_harmonizer_v4" in config.metadata; set
        run_name/thread_id for grouping.
      - Use a dedicated LangSmith project for V4 to separate from V3; keep LANGCHAIN_TRACING_V2=true.
  4. Evaluator alignment:
      - Grade against the structured financial tool output (not scraped messages).
      - Keep writing financial_eval feedback to runs so LangSmith comparisons (V3 vs V4) are easy.
  5. After the StepFunction finishes:
      - Snapshot the V3 runs as a LangSmith dataset and re-run the evaluator on V4 outputs for apples-
        to-apples comparison.

  If you paste the V4 plan into V4_PLAN.md in the clean repo and proceed with these changes, you’ll get
  clearer LangSmith feedback and more reliable financial validation.
