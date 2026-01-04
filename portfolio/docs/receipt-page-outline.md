# Receipt Page Content Guide

## Opening Hook
- Your motivation: wanted to improve at programming, ML, and LLMs
- This project touches all three
- Brief mention of what the system does (processes receipt photos → structured data)

---

## Section 1: CI/CD Foundation

**Key points to cover:**
- Why you needed automated pipelines before doing ML work
- What you use: GitHub Actions, Pulumi, automated testing
- The benefit: fast iteration, push and forget, focus on the interesting problems
- Optional: mention any pain points you solved (flaky tests, deployment issues)

---

## Section 2: Unsupervised ML - Line Clustering

**Key points to cover:**
- The problem: phone photos are messy (skewed, angled, varying quality)
- What you do: cluster text lines by geometric properties
- Why it's unsupervised: no labels, algorithm discovers structure from the data
- The output: perspective transform to straighten the image
- Why it matters: garbage in → garbage out; clean input helps everything downstream

---

## Section 3: Supervised ML - LayoutLM

**Key points to cover:**
- The problem: OCR gives you text, but not meaning
- What LayoutLM does: classifies tokens (MERCHANT, TOTAL, DATE, etc.)
- Why it's supervised: trained on labeled examples
- Precision vs recall trade-off (tie in the dartboard visualization here)
- Training infrastructure: SageMaker, metrics in DynamoDB
- What you learned about model training

---

## Section 4: LLM Integration

**Key points to cover:**
- Hyperparameter tuning: LLM reviews results, suggests next experiments
- Why LLMs work well here: judgment calls, pattern recognition across runs
- Receipt Agent (if you want to mention it): conversational querying
- How this differs from the ML models: LLMs for fuzzy/strategic decisions, ML for repeatable classification

---

## Closing
- How the layers connect: CI/CD → Unsupervised → Supervised → LLM
- What you learned / what surprised you
- What's next (optional)

---

## Dartboard Placement Suggestion

Insert `<PrecisionRecallDartboard />` in Section 3 when you introduce precision/recall. Lead into it with something like "Think of it like throwing darts..."

---

## Tone Notes
- First person, conversational
- Specific examples from your actual experience
- Avoid jargon dumps - explain why each piece matters
- Your voice, your struggles, your learnings
