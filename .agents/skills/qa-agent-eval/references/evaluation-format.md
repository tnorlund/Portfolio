# Per-question evaluation format

Each `qa-eval-q{NN}/qa_evaluation/q{NN}.md` follows this structure:

```markdown
# Q{N}: {question text}

## Metadata
| Field | Value |
| Question Index / Trace ID / Cost / LLM Calls / Tool Invocations / Receipts |

## Plan
## Tool Calls
## Agent Reasoning
## Shape
## Final Answer
### Evidence

## Evaluation
**Grade:** {A-F with +/-}
**Correct Answer:** {MCP-verified ground truth}
**Tool Efficiency:** {N} calls ({assessment})
**Issues:** {numbered list}
**Improvements from Baseline:** {comparison to previous run}
```

Grading rubric:

- A: correct answer with proper evidence, matches MCP verification.
- B: mostly correct, minor issues (slightly off amounts, missing some evidence).
- C: partially correct or missing important context.
- D: significant errors in answer or evidence.
- F: wrong answer or critical data loss.

Check whether the synthesizer agrees with the agent's reasoning. Agent correct
but synthesizer contradicts it → D or F (synthesis override bug). If they agree,
grade the agent's own analysis.
