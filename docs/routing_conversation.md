# OpenRouter GPT-OSS-120B Conversation

A conversation with the `gpt-oss-120b (free)` model on OpenRouter about API usage, rate limits, scaling, and integrating with LangChain/LangGraph and AWS Step Functions.

---

## Exchange 1: OpenRouter Credits and Free API Requests

### Tyler (User)

I just signed up for open router, and I got an email saying if I buy $10 in credits, I'd get 1000 free API requests per day. The purpose of me signing up for Openrouter was to use the GPT OSS 120B model. Are these 1000 API requests for the OSS model? How do I view my usage?

### GPT-OSS-120B (Assistant)

### Quick answer

- **The 1,000 "free API requests per day" you get when you purchase $10 credits is **not** tied specifically to the OSS‑120B model.**
- The OSS‑120B model (and the other "OSS" models on OpenRouter) are **free to use** and are **not counted against that 1,000‑request daily quota**.
- The quota applies to **paid models** (e.g., Claude, GPT‑4, Llama‑2‑70B, etc.) that consume credits.

---

### How OpenRouter's credit & request system works

| Tier | What you pay for | What you get (per day) | How usage is measured |
|------|------------------|------------------------|----------------------|
| **Free tier** | $0 | 0 credits, but you can still call any **OSS** model (GPT‑OSS‑120B, Llama‑2‑13B‑OSS, etc.) – no token‑based cost. | No daily request limit for OSS models, only a **rate‑limit** (≈ 10 req/s per IP). |
| **Paid tier** (e.g., $10 credit) | $10 buys you 1,000 credits **per day** (the exact number of "free requests" depends on the model's per‑token price). | You can call **any paid model** up to the credit limit. | Credits are deducted based on **tokens** (prompt + completion). The "1,000 free requests" wording is a simplification; each request may consume anywhere from a few cents to several dollars worth of credits. |
| **Enterprise / higher‑volume plans** | Custom pricing | Higher daily credit caps, priority support, etc. | Same token‑based accounting. |

> **Bottom line:** As long as you stay with the OSS models, you won't see any credit deduction and you won't be limited to 1,000 requests per day. The limit only matters when you start using the paid, non‑OSS models.

---

### Where to see your usage

1. **Log in to the OpenRouter dashboard**
   [https://openrouter.ai/dashboard](https://openrouter.ai/dashboard)

2. **Navigate to the "Usage" tab** (usually on the left‑hand sidebar).
   You'll see a table broken down by:
   - **Model name** (e.g., `gpt-oss-120b`, `claude-2`, `gpt-4`)
   - **Tokens used** (prompt + completion)
   - **Credits spent** (if any)
   - **Number of requests**

3. **Filter / sort**
   - Use the dropdown to show only "OSS" models if you want to verify that they are not consuming credits.
   - The daily quota bar at the top of the page shows how many **paid‑model credits** you have left for the current day.

4. **Programmatic access (optional)**
   OpenRouter also exposes a **usage endpoint** you can call with your API key:

```bash
# Replace YOUR_API_KEY with your actual OpenRouter API key
curl -H "Authorization: Bearer YOUR_API_KEY" \
     https://openrouter.ai/api/v1/usage
```

The JSON response contains the same fields you see on the dashboard (tokens, credits, request counts per model).

---

### Example: Checking that OSS‑120B isn't using credits

```bash
# 1️⃣ Make a request to the OSS model
curl -X POST https://openrouter.ai/api/v1/chat/completions \
     -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
           "model": "gpt-oss-120b",
           "messages": [{"role":"user","content":"Hello!"}]
         }'

# 2️⃣ Query usage
curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     https://openrouter.ai/api/v1/usage
```

You'll see an entry like:

```json
{
  "model": "gpt-oss-120b",
  "tokens_used": 12,
  "credits_spent": 0,
  "requests": 1
}
```

The `credits_spent` field stays at `0` for OSS models, confirming they are free.

---

### Frequently asked questions

| Question | Answer |
|----------|--------|
| **Do I need to buy the $10 credit to use OSS‑120B?** | No. The OSS models are completely free; you can start using them right after you verify your email. |
| **What happens if I exceed the rate‑limit for OSS models?** | You'll receive a `429 Too Many Requests` response. Back‑off for a second or two and retry. |
| **Can I see a per‑model breakdown of token usage?** | Yes – the "Usage" page lets you filter by model, and the API `/usage` endpoint returns the same breakdown. |
| **Will the free‑request quota ever apply to OSS models?** | As of the current OpenRouter policy, no. Only paid models consume the daily credit quota. |
| **What if I want to monitor usage in real‑time?** | Use the `/v1/usage` endpoint after each request, or set up a webhook (if you have a Pro/Enterprise plan) that posts usage events to your own server. |

---

### TL;DR

- **OSS‑120B is free** and **does not count toward the 1,000‑request daily limit** you get with $10 credits.
- The 1,000‑request limit applies only to **paid models** that consume credits.
- Check your usage on the **OpenRouter dashboard → Usage** page, or via the **`/v1/usage` API endpoint**.

Enjoy experimenting with the massive GPT‑OSS‑120B model! 🚀

---

## Exchange 2: Using the OSS Model via API

### Tyler (User)

Ok. so Can I use this gpt-oss-120b (free) model through the API? I'm currently talking to you through the open router console.

### GPT-OSS-120B (Assistant)

## Yes – you can call `gpt‑oss‑120b` via the OpenRouter HTTP API

Below is a step‑by‑step guide that shows how to:

1. **Get your API key** (if you don't already have one)
2. **Make a request** to the OSS model with `curl` and with Python (`requests`)
3. **Check that the request didn't consume any credits**
4. **Monitor usage** in the dashboard or programmatically

---

### 1️⃣ Get / verify your OpenRouter API key

| Where | What to do |
|-------|------------|
| **Dashboard** → **API Keys** | Click **"Create new key"** (or copy an existing one). The key looks like `or_XXXXXXXXXXXXXXXXXXXXXXXX`. |
| **Email verification** | Make sure you've confirmed the email address you used to sign up – otherwise the key will be disabled. |

> **Tip:** Keep the key secret; treat it like a password. Do **not** commit it to public repos.

---

### 2️⃣ Make a request to `gpt‑oss‑120b`

#### a) Using `curl`

```bash
# Replace YOUR_API_KEY with your actual OpenRouter API key
curl -X POST https://openrouter.ai/api/v1/chat/completions \
     -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
           "model": "gpt-oss-120b",
           "messages": [
             {"role": "system", "content": "You are a helpful assistant."},
             {"role": "user",   "content": "Explain why the sky is blue in one sentence."}
           ],
           "max_tokens": 64,
           "temperature": 0.7
         }'
```

#### b) Using Python (`requests`)

```python
import requests
import json

API_KEY = "YOUR_API_KEY"  # Replace with your actual OpenRouter API key
url = "https://openrouter.ai/api/v1/chat/completions"

payload = {
    "model": "gpt-oss-120b",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Explain why the sky is blue in one sentence."}
    ],
    "max_tokens": 64,
    "temperature": 0.7
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

response = requests.post(url, headers=headers, json=payload)
print(json.dumps(response.json(), indent=2))
```

**What you'll see** – a JSON response containing `choices[0].message.content` with the model's answer.

---

### 3️⃣ Verify that no credits were spent

OpenRouter tracks usage per‑model. After you've sent a request, you can:

#### a) Check the dashboard

1. Log in → **Dashboard → Usage**.
2. Filter by **Model = `gpt-oss-120b`**.
3. You'll see **`credits_spent = 0`** (tokens are still counted for rate‑limit purposes, but they don't cost you anything).

#### b) Call the usage endpoint programmatically

```bash
# Replace YOUR_API_KEY with your actual OpenRouter API key
curl -H "Authorization: Bearer YOUR_API_KEY" \
     https://openrouter.ai/api/v1/usage
```

Sample JSON snippet for the OSS model:

```json
{
  "model": "gpt-oss-120b",
  "tokens_used": 23,
  "credits_spent": 0,
  "requests": 1
}
```

If `credits_spent` stays at `0`, you're good – the request is free.

---

### 4️⃣ Rate limits & best‑practice tips

| Limit type | Value for OSS models | How to handle it |
|------------|---------------------|------------------|
| **Requests per second** | ~10 req / s per IP (soft limit) | Add a short `sleep(0.1)` between rapid calls or implement exponential back‑off on `429` responses. |
| **Daily request quota** | **None** (free models are unlimited) | Only paid models count toward the $10‑credit "1,000 free requests" quota. |
| **Token length** | Up to 8k tokens total (prompt + completion) | Truncate or chunk very long inputs. |

**Typical error handling pattern (Python):**

```python
import time, requests

def call_oss(payload):
    while True:
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code == 429:          # rate‑limit hit
            retry_after = int(resp.headers.get("Retry-After", "1"))
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
```

---

### 5️⃣ Quick checklist before you start

- [ ] API key created and stored securely.
- [ ] Model name set exactly to `"gpt-oss-120b"` (case‑sensitive).
- [ ] `Authorization: Bearer <key>` header present.
- [ ] You're looking at the **Usage** page (or `/v1/usage`) to confirm `credits_spent = 0`.
- [ ] Your code respects the ~10 req/s rate limit (or catches `429`).

---

## TL;DR

- **Yes** – `gpt‑oss‑120b` is fully accessible via the OpenRouter API.
- It is **free**: no credits are deducted, and there is **no daily request cap** (only a modest rate‑limit).
- Use the standard `/v1/chat/completions` endpoint with your API key, then verify usage on the dashboard or via the `/v1/usage` endpoint.

Happy coding, and enjoy the power of a 120‑billion‑parameter model without spending a dime! 🚀

---

## Exchange 3: Best Practices for Deploying at Scale

### Tyler (User)

So what are best practices for deploying this at scale? I've been hitting rate-limit and concurrency problems while using this model on Ollama Cloud. Do I fall back to open router? I already have $10 in credits for open router. When do I reach the limit for the OSS 120B model free through open router?

### GPT-OSS-120B (Assistant)

## Scaling `gpt‑oss‑120b` on OpenRouter – What you need to know

| Topic | Key point | Practical tip |
|-------|-----------|--------------|
| **Free‑model limits** | No *daily* request quota, but **rate‑limit ≈ 10 req / s per IP** (soft limit). | Use a request‑throttling layer (token‑bucket, queue, or async semaphore). |
| **Credits** | Your $10 = ~1,000 credits **only apply to paid models**. OSS models never consume credits. | You can keep the $10 for fallback to paid models (Claude, GPT‑4, etc.) when you truly need more throughput. |
| **Concurrency** | OpenRouter caps concurrent connections per IP (≈ 5‑10). Exceeding it returns **429 Too Many Requests**. | Serialize calls per worker or spread traffic across multiple IPs / API keys. |
| **Ollama Cloud vs OpenRouter** | Ollama Cloud runs the model *yourself* (or on a managed node) → you control scaling, but you pay for compute. OpenRouter → you pay per‑token (or nothing for OSS) but you are limited by their public rate‑limit. | Use Ollama for high‑throughput, low‑latency workloads; keep OpenRouter as a cheap "burst" or fallback. |
| **When you *hit* the limit** | You'll see HTTP 429 with a `Retry‑After` header (usually 1‑2 s). | Implement exponential back‑off and respect `Retry‑After`. |
| **Monitoring** | Dashboard → **Usage** shows tokens, requests, and credits per model. | Call `GET https://openrouter.ai/api/v1/usage` after each batch to verify you're still at `credits_spent = 0`. |

Below is a **complete playbook** for running the OSS model at scale while staying inside OpenRouter's limits, plus a fallback strategy to Ollama Cloud or paid OpenRouter models when you outgrow the free tier.

---

## 1️⃣ Understand the real limits

### Rate‑limit (soft)

- **≈ 10 requests per second per source IP**.
- If you exceed it, OpenRouter returns **429** with a `Retry‑After` header (usually 1 s).

### Concurrency

- The service caps **simultaneous TCP connections** per IP (≈ 5‑10).
- Even if you stay under 10 rps, opening many parallel HTTP streams can trigger the limit.

### Token limits

- **Maximum context length** for `gpt‑oss‑120b` is **8k tokens** (prompt + completion).
- Tokens are **not charged**, but they count toward the per‑request size limit.

> **Bottom line:** The only hard ceiling you'll hit on the free OSS model is **throughput** (rps) and **concurrent connections**, not a daily request count.

---

## 2️⃣ Architecture for high‑throughput usage

Below is a **recommended stack** that works for most production workloads:

```text
┌─────────────────────┐
│  Client / Front‑end │
└───────┬─────────────┘
        │   (HTTP/HTTPS)
        ▼
┌─────────────────────┐
│  API Gateway /      │   ← Rate‑limit & auth (e.g., Kong, Nginx, Cloudflare Workers)
│  Edge Proxy         │
└───────┬─────────────┘
        │
        ▼
┌─────────────────────┐
│  Request Queue      │   ← FIFO, max‑concurrency (Redis‑RQ, RabbitMQ, SQS)
│  (e.g., Celery,     │
│   BullMQ, Sidekiq)  │
└───────┬─────────────┘
        │
        ▼
┌─────────────────────┐
│  Worker Pool        │   ← 1‑5 async workers per VM (Node, Python, Go)
│  (async HTTP client)│
└───────┬─────────────┘
        │
        ▼
┌─────────────────────┐
│  OpenRouter API     │   ← gpt‑oss‑120b
└─────────────────────┘
```

### Why this works

| Component | What it solves |
|-----------|----------------|
| **API Gateway** | Central point to enforce a global rate‑limit (e.g., 8 rps) before traffic even reaches your workers. |
| **Request Queue** | Guarantees *ordered* processing and caps the number of concurrent OpenRouter calls. |
| **Worker Pool** | Each worker uses a **single persistent HTTP/2 connection** (or a small connection pool) to stay under the concurrency cap. |
| **Back‑off logic** | Workers automatically retry on 429 using the `Retry‑After` header. |

---

## 3️⃣ Code snippets – a production‑ready async client

Below is a **Python‑asyncio** example that respects OpenRouter's limits, retries on 429, and logs usage. You can translate the same ideas to Node, Go, or Rust.

```python
import asyncio
import aiohttp
import time
import os
from collections import deque

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# -------------------------------------------------
# 1️⃣ Token‑bucket rate limiter (≈ 8 rps, safe margin)
# -------------------------------------------------
MAX_RPS = 8
bucket = deque(maxlen=MAX_RPS)   # timestamps of recent requests

async def acquire_slot():
    while len(bucket) >= MAX_RPS:
        # wait until the oldest request is >1 s old
        await asyncio.sleep(0.1)
        now = time.time()
        while bucket and now - bucket[0] > 1.0:
            bucket.popleft()
    bucket.append(time.time())

# -------------------------------------------------
# 2️⃣ Core request function with exponential back‑off
# -------------------------------------------------
async def call_oss(messages, max_tokens=256, temperature=0.7):
    payload = {
        "model": "gpt-oss-120b",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    backoff = 1.0
    for attempt in range(5):          # max 5 retries
        await acquire_slot()          # respect global RPS limit
        async with aiohttp.ClientSession() as session:
            async with session.post(
                BASE_URL, json=payload, headers=HEADERS
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                if resp.status == 429:
                    # Respect Retry‑After if present
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else backoff
                    await asyncio.sleep(wait)
                    backoff = min(backoff * 2, 30)   # cap at 30 s
                    continue
                # For other errors, raise an exception
                txt = await resp.text()
                raise RuntimeError(f"{resp.status}: {txt}")

    raise RuntimeError("Exceeded max retries on OpenRouter")

# -------------------------------------------------
# 3️⃣ Example usage (run many calls concurrently)
# -------------------------------------------------
async def main():
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user",   "content": "Explain the difference between TCP and UDP."},
    ]

    # launch 30 concurrent calls (will be throttled to ≤8 rps)
    tasks = [call_oss(msgs) for _ in range(30)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"❌ Call {i} failed: {r}")
        else:
            print(f"✅ Call {i} answer: {r['choices'][0]['message']['content'][:60]}…")

if __name__ == "__main__":
    asyncio.run(main())
```

### What this does

- **Rate‑limits** to ≤ 8 rps (a safe margin under the 10 rps public limit).
- **Retries** on 429 with exponential back‑off, respecting `Retry‑After`.
- **Keeps a single session per request** – you can further optimize by re‑using a global `ClientSession` if you have many workers on the same process.

---

## 4️⃣ Scaling beyond the free‑model limits

| Situation | Recommended action |
|-----------|--------------------|
| **You need > 10 rps** (or many parallel connections) | **Option A – Self‑host** the OSS model with **Ollama Cloud** or your own GPU nodes. You control the concurrency and can spin up many workers. **Option B – Distribute traffic** across **multiple API keys** (each key gets its own IP‑rate‑limit). Rotate keys in a round‑robin fashion. |
| **Latency matters** (sub‑200 ms) | Self‑host on a region close to your users (e.g., AWS us‑east‑1, GCP europe‑west1). OpenRouter adds an extra hop and can be ~100‑200 ms slower. |
| **You want a safety net** (if OSS is overloaded) | Keep your **$10 credit** and configure a **fallback policy**: if a request to OSS fails after 3 retries, automatically retry on a paid model (e.g., `claude-2`). This ensures continuity while you stay within budget. |
| **You need higher token limits** (e.g., 16k context) | OSS‑120B caps at 8k. Switch to a paid model that supports larger contexts (e.g., `gpt‑4‑32k`). Use the same request‑queue infrastructure; only the **credits** will be deducted. |

### Example fallback logic (Python)

```python
async def robust_call(messages):
    try:
        return await call_oss(messages)
    except RuntimeError as e:
        print("OSS failed, falling back to Claude:", e)
        # Simple fallback to a paid model (credits will be used)
        return await call_paid_model(messages, model="claude-2")
```

---

## 5️⃣ Monitoring & Alerting

1. **Dashboard** – OpenRouter → **Usage** → filter `gpt‑oss‑120b`.
   - Look for spikes in **requests per minute**.
   - Verify `credits_spent` stays at `0`.

2. **Programmatic health check** – poll `/v1/usage` every minute and push metrics to Prometheus / CloudWatch.

```bash
curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     https://openrouter.ai/api/v1/usage | jq .
```

3. **Alert** – trigger a webhook or email when:
   - `requests_last_minute > 9` (approaching the rate limit).
   - `429` responses exceed a threshold (e.g., > 5% of calls).

---

## 6️⃣ Quick "cheat‑sheet" for your current situation

| Question | Answer |
|----------|--------|
| **Can I keep using the free OSS model on OpenRouter?** | Yes – it never consumes your $10 credits and has **no daily request quota**. |
| **When will I actually hit a limit?** | When you exceed **≈ 10 req / s** or open **> 5‑10 concurrent connections** from the same IP. |
| **What does my $10 buy?** | ~1,000 credits per day for **paid** models (Claude, GPT‑4, Llama‑2‑70B, etc.). It does **not** affect the OSS model. |
| **Should I fall back to Ollama Cloud?** | Use Ollama if you need **higher throughput, lower latency, or larger context**. Keep OpenRouter as a cheap "burst" or fallback for occasional traffic. |
| **Best‑practice summary** | 1️⃣ Throttle to ≤ 8 rps per IP. 2️⃣ Queue & limit concurrency (≤ 5‑10). 3️⃣ Retry on 429 with exponential back‑off. 4️⃣ Monitor usage via dashboard or `/v1/usage`. 5️⃣ Keep $10 credits for paid‑model fallback or for when you outgrow the free limits. |

---

## 7️⃣ TL;DR (one‑paragraph)

The **OSS‑120B model on OpenRouter is completely free** and has **no daily request cap**, but it is limited to **≈ 10 requests per second per IP** and a modest number of concurrent connections. To scale, place a rate‑limiter (≈ 8 rps) and a request queue in front of a small pool of async workers that respect `429 Retry‑After`. If you need higher throughput, lower latency, or larger context windows, move the workload to **Ollama Cloud** (self‑hosted) and keep OpenRouter as a cheap fallback. Your $10 credit only applies to **paid models**; the OSS model will never consume it. Monitor usage via the OpenRouter dashboard or the `/v1/usage` endpoint, and set alerts for 429 spikes. With these patterns you can run the 120‑billion‑parameter model reliably at production scale. 🚀

---

## Exchange 4: LangChain and LangGraph Integration

### Tyler (User)

I'm using Langchain and langgraph. Is there anything that's built into that dependency for this?

### GPT-OSS-120B (Assistant)

## LangChain Integration Summary

**Yes – LangChain + LangGraph already give you most of the plumbing you need to stay inside OpenRouter's free‑model limits.**

| What you need to control | Built‑in LangChain / LangGraph feature | How to use it (code snippet) |
|--------------------------|----------------------------------------|------------------------------|
| **API endpoint & model name** | `ChatOpenAI` (or `OpenAI`) can be pointed at any OpenAI‑compatible endpoint. | `ChatOpenAI(base_url="https://openrouter.ai/api/v1", model="gpt-oss-120b", ...)` |
| **Rate‑limit (≈ 10 req / s per IP)** | `RunnableWithRetry` + a **semaphore‑based rate limiter** (you can wrap the LLM in a `Runnable` that acquires a token from an `asyncio.Semaphore`). | See the *Rate‑limited LLM* example below. |
| **Automatic retries on 429 / network errors** | `RunnableRetry` (or `RunnableWithRetry` in older versions) uses **tenacity** under the hood. | `llm_retry = RunnableRetry(llm, retry_if_exception_type=OpenAIError, ...)` |
| **Fallback to a paid model when the free model is overloaded** | `RunnableWithFallback` – you give it a primary LLM and a secondary LLM that will be called if the primary raises an exception. | `fallback_llm = RunnableWithFallback(primary=llm_free, fallback=llm_paid)` |
| **Token‑usage monitoring** | **Callback handlers** (`OpenAICallbackHandler`, `StreamingStdOutCallbackHandler`, custom `BaseCallbackHandler`). They receive `on_llm_start`, `on_llm_end`, `on_token` events that include `prompt_tokens`, `completion_tokens`, and `total_tokens`. | Register the handler in the `callbacks` argument of the LLM or the graph. |
| **Concurrency control inside a LangGraph** | Each node is just a `Runnable`. You can wrap the node with a **semaphore** or use the built‑in `graph.add_edge(..., condition=…)` to serialize calls. | See the *LangGraph node with semaphore* example. |

Below is a **complete, ready‑to‑copy** example that puts all of these pieces together:

---

## 1️⃣ Set up the OpenRouter LLM in LangChain

```python
# pip install langchain openai langgraph tenacity aiohttp
import os
from langchain_openai import ChatOpenAI
from langchain_core.runnables import Runnable, RunnableLambda, RunnableRetry, RunnableWithFallback
from openai import OpenAIError   # the exception type raised by the OpenAI client

# ----------------------------------------------------------------------
# 1️⃣  Create a ChatOpenAI that talks to OpenRouter's OSS model
# ----------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")   # e.g. or_XXXXXXXXXXXXXXXXX

llm_free = ChatOpenAI(
    model="gpt-oss-120b",                     # exact model name on OpenRouter
    openai_api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",  # OpenRouter's OpenAI‑compatible endpoint
    temperature=0.7,
    max_tokens=512,
    request_timeout=30,                       # avoid hanging forever
    # optional: you can add any extra OpenRouter‑specific headers here
    # e.g. {"HTTP-Referer": "https://my‑app.com", "X-Title": "MyApp"}
)
```

> **Note:** The `ChatOpenAI` class is just a thin wrapper around the official `openai` Python client, so everything you can configure there (proxy, timeout, `max_retries`, etc.) works the same way.

---

## 2️⃣ Add a **rate‑limiter** (≈ 8 req / s is a safe margin)

```python
import asyncio
from typing import Any, Dict

# ----------------------------------------------------------------------
# 2️⃣  Semaphore‑based rate limiter (async)
# ----------------------------------------------------------------------
MAX_RPS = 8                     # stay under OpenRouter's 10 rps soft limit
_semaphore = asyncio.Semaphore(MAX_RPS)

async def _rate_limited_call(input_: Dict[str, Any]) -> Dict[str, Any]:
    async with _semaphore:
        # The underlying LLM is async‑compatible, so we just await it.
        return await llm_free.ainvoke(input_)

# Wrap the async function as a LangChain Runnable
rate_limited_llm = RunnableLambda(_rate_limited_call)
```

If you prefer a **synchronous** environment, replace the semaphore with the `ratelimit` package:

```python
from ratelimit import limits, sleep_and_retry

@sleep_and_retry
@limits(calls=MAX_RPS, period=1)   # 8 calls per 1 second
def sync_rate_limited_call(input_: dict) -> dict:
    return llm_free.invoke(input_)
```

---

## 3️⃣ Automatic **retries** on 429 / transient errors

```python
# ----------------------------------------------------------------------
# 3️⃣  Retry wrapper (exponential back‑off, max 5 attempts)
# ----------------------------------------------------------------------
llm_retry = RunnableRetry(
    rate_limited_llm,
    retry_if_exception_type=OpenAIError,   # catches 429, 500, timeout, etc.
    max_attempts=5,
    wait_exponential_multiplier=1,         # 1 s, 2 s, 4 s, …
    wait_exponential_max=30,
)
```

`RunnableRetry` uses **tenacity** under the hood, so you can pass any tenacity arguments (`stop`, `wait`, `retry_error_callback`, …) if you need more control.

---

## 4️⃣ **Fallback** to a paid model (e.g., Claude‑2) when the free model is exhausted

```python
# ----------------------------------------------------------------------
# 4️⃣  Paid fallback LLM (will consume your $10 credits)
# ----------------------------------------------------------------------
llm_paid = ChatOpenAI(
    model="claude-2",                     # any paid model on OpenRouter
    openai_api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    temperature=0.7,
    max_tokens=512,
)

fallback_llm = RunnableWithFallback(
    primary=llm_retry,
    fallback=llm_paid,
    # The fallback is triggered when the primary raises ANY exception.
    # You can narrow it down with `fallback_exceptions=(OpenAIError,)`.
)
```

Now **every request** goes to the free OSS model first; if it fails (rate‑limit, overload, etc.) the paid model automatically picks up and you'll see the credit usage in the OpenRouter dashboard.

---

## 5️⃣ Hook **usage‑monitoring** into LangChain callbacks

```python
from langchain.callbacks.base import BaseCallbackHandler
from datetime import datetime

class TokenUsageHandler(BaseCallbackHandler):
    """Collect token usage for every LLM call."""
    def __init__(self):
        self.records = []   # you could push to a DB, Prometheus, etc.

    def on_llm_end(self, response, **kwargs):
        # `response` is a LangChain LLMResult
        usage = response.llm_output.get("token_usage", {})
        self.records.append({
            "timestamp": datetime.utcnow().isoformat(),
            "model": response.llm_output.get("model_name"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "credits_spent": usage.get("credits_spent", 0),   # only for paid models
        })

# Register the handler when you build the graph (or when you call the LLM directly)
usage_handler = TokenUsageHandler()
```

Pass the handler to any LangChain `Runnable` via the `callbacks` argument:

```python
result = fallback_llm.invoke(
    {"messages": [{"role": "user", "content": "Why is the sky blue?"}]},
    callbacks=[usage_handler],
)
```

You can later inspect `usage_handler.records` or ship them to a monitoring system (Grafana, CloudWatch, etc.).

---

## 6️⃣ Plug everything into **LangGraph**

Below is a minimal **state‑graph** that:

1. Receives a user message.
2. Calls the **rate‑limited, retry‑wrapped, fallback‑enabled LLM**.
3. Stores the assistant's reply in the graph state.

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

class ChatState(TypedDict):
    messages: List[Dict[str, str]]   # the full chat history

# ----------------------------------------------------------------------
# 6️⃣  Define the graph
# ----------------------------------------------------------------------
graph = StateGraph(ChatState)

# Node that calls the LLM
async def llm_node(state: ChatState):
    # The LLM expects a dict with a "messages" key
    response = await fallback_llm.ainvoke(
        {"messages": state["messages"]},
        callbacks=[usage_handler],
    )
    # Append the assistant's reply to the history
    assistant_msg = response["choices"][0]["message"]
    state["messages"].append(assistant_msg)
    return {"messages": state["messages"]}

graph.add_node("call_llm", llm_node)

# Simple linear flow: start → call_llm → END
graph.set_entry_point("call_llm")
graph.add_edge("call_llm", END)

app = graph.compile()
```

### Running the graph

```python
# First user turn
initial_state = {"messages": [{"role": "user", "content": "Explain quantum tunnelling in two sentences."}]}
final_state = app.invoke(initial_state)

print(final_state["messages"][-1]["content"])   # assistant answer
```

Because `call_llm` is **already wrapped** with the semaphore, retry, and fallback logic, you get:

- **Rate‑limit safety** – never more than 8 req / s from this process.
- **Automatic retries** – 429 → exponential back‑off → retry up to 5 times.
- **Graceful fallback** – if the free model is truly unavailable, Claude‑2 (or any paid model you choose) will answer, and the credit usage will be logged by the callback handler.

---

## 7️⃣ What you still need to handle yourself

| Concern | Why it's not fully covered | Recommended approach |
|---------|---------------------------|---------------------|
| **Distributed rate‑limit across many workers / containers** | LangChain's semaphore only works *inside* a single process. | Use a **central token bucket** (Redis `INCR` with TTL, or a hosted rate‑limit service) and have each worker acquire a token before calling the LLM. |
| **Long‑running batch jobs** (e.g., 10k requests in a night) | The per‑second limit still applies, so you must throttle the whole batch. | Schedule the batch with a **cron** that respects the same semaphore, or use `asyncio.sleep` between chunks. |
| **Persisting usage metrics** | Callback handler stores data only in memory. | Write the `usage_handler.records` to a DB (PostgreSQL, DynamoDB) or push to a metrics system (Prometheus `counter`/`histogram`). |
| **Dynamic fallback policies** (e.g., "use paid model only after 3 consecutive 429s") | `RunnableWithFallback` triggers on *any* exception. | Write a small wrapper `RunnableLambda` that counts consecutive failures and raises a custom exception only after the threshold, then feed that into `RunnableWithFallback`. |

---

## 8️⃣ Quick checklist for production

| ✅ Item | How to verify |
|---------|--------------|
| **API key is set** | `echo $OPENROUTER_API_KEY` → non‑empty. |
| **Base URL points to OpenRouter** | `llm_free.base_url == "https://openrouter.ai/api/v1"` |
| **Rate‑limit is enforced** | Run a load test (e.g., 50 parallel calls). You should see **no more than 8 req / s** in the logs and occasional `429` that are automatically retried. |
| **Retries work** | Force a temporary network error (e.g., disconnect internet) and watch the exponential back‑off in the logs. |
| **Fallback works** | Temporarily set `MAX_RPS = 1` (so the free model always hits 429) and confirm the response comes from the paid model (check `model` field in the result). |
| **Token usage is logged** | After a few calls, inspect `usage_handler.records` – you should see `total_tokens` and `credits_spent = 0` for the OSS model, and non‑zero credits when the fallback fires. |
| **Distributed throttling** (if you have >1 worker) | Verify that a Redis key (e.g., `openrouter:tokens`) never exceeds `MAX_RPS` per second across all workers. |

---

## 9️⃣ Final words

- **LangChain + LangGraph give you the building blocks** (LLM wrapper, retry, fallback, callbacks).
- The **only piece you have to add** for the free OSS model is a **rate‑limiter** (semaphore or external token bucket) because OpenRouter's limit is per‑IP, not per‑model.
- With the pattern above you can **run the 120‑B OSS model at production scale**, stay safely under the 10 rps limit, automatically fall back to a paid model when needed, and have **full visibility** into token usage and credit consumption.

Happy building! 🚀

---

## Exchange 5: AWS Step Functions with Parallel LangGraph Steps

### Tyler (User)

I could see this helping, but I have an AWS step function that runs many of the langgraph steps in parallel. The state is saved in S3 and the traces are managed through the business logic. How would I be able to do this if that stuff isn't accessible through memory?

### GPT-OSS-120B (Assistant)

## Overview

You have an **AWS Step Functions** workflow that:

1. Executes many **LangGraph** nodes **in parallel** (via a `Map` state).
2. Persists the graph state in **S3**.
3. Handles its own tracing / business‑logic bookkeeping.

The OpenRouter **OSS‑120B** model is free, but it is still limited to **≈ 10 req / s per source IP** (and a small concurrent‑connection cap).
Because your workers are **stateless Lambdas / containers** that do **not share memory**, you need a **centralised coordination point** for:

| Goal | What you need |
|------|--------------|
| **Global rate‑limit** (≤ 10 rps) | Distributed token‑bucket / leaky‑bucket stored in a service that all workers can atomically read‑modify‑write (DynamoDB, Redis, or DynamoDB‑based "distributed semaphore"). |
| **Retry on 429 / transient errors** | Built‑in LangChain `RunnableRetry` + a wrapper that respects the central token bucket before each call. |
| **Fallback to a paid model** | `RunnableWithFallback` that automatically switches when the free call raises an exception. |
| **Usage / credit tracking** | Each worker writes a tiny JSON line to an **S3 "usage log"** (or a DynamoDB table) after every request; a downstream aggregation job (Athena, Glue, or a simple Lambda) produces daily reports. |
| **Visibility in Step Functions** | Emit **Step Functions task‑level "heartbeat" / "callback"** events (via `TaskToken` or `CallbackPattern`) so you can see when a request is throttled, retried, or fell back. |

Below is a **complete pattern** you can drop into your existing Step Functions + LangGraph stack.

---

## 1️⃣ Centralised Rate‑Limiter (Distributed Token Bucket)

### Why DynamoDB?

- **Strongly consistent reads/writes** → you can atomically decrement a counter.
- **Serverless** – no extra infra to manage.
- **Free tier** (25 GB storage, 200 M reads/writes) is more than enough for a few thousand requests per day.

### Table schema

| PK (partition key) | Sort key | Attribute | Meaning |
|--------------------|----------|-----------|---------|
| `rateLimiter` | `oss120b` | `tokens` | Integer – number of tokens currently available (max = MAX_TOKENS). |
| `rateLimiter` | `oss120b_ts` | `reset_ts` | Epoch seconds when the bucket will be refilled next. |

Create the table (via CloudFormation, CDK, or console) with **pay‑per‑request** billing.

### Helper library (Python)

```python
import time
import boto3
from botocore.exceptions import ClientError

ddb = boto3.resource("dynamodb")
TABLE_NAME = "OpenRouterRateLimiter"
TABLE = ddb.Table(TABLE_NAME)

MAX_TOKENS = 8          # safe margin under the 10 rps public limit
REFILL_INTERVAL = 1.0   # seconds (1 token per 1 s)

class DistributedRateLimiter:
    """Leaky‑bucket implementation using DynamoDB atomic updates."""
    def __init__(self, pk="rateLimiter", sk="oss120b"):
        self.pk = pk
        self.sk = sk

    def _refill_if_needed(self, now: float, item: dict) -> dict:
        """Refill tokens based on elapsed time."""
        last_reset = float(item.get("reset_ts", "0"))
        elapsed = now - last_reset
        if elapsed >= REFILL_INTERVAL:
            # Compute how many tokens to add (floor(elapsed / interval))
            add = int(elapsed // REFILL_INTERVAL)
            new_tokens = min(item["tokens"] + add, MAX_TOKENS)
            new_reset = last_reset + add * REFILL_INTERVAL
            return {"tokens": new_tokens, "reset_ts": new_reset}
        return item

    def acquire(self, timeout: float = 10.0) -> bool:
        """Block until a token is obtained or timeout expires."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            now = time.time()
            try:
                # 1️⃣ Read current bucket (strongly consistent)
                resp = TABLE.get_item(
                    Key={"pk": self.pk, "sk": self.sk},
                    ConsistentRead=True,
                )
                item = resp.get("Item", {"tokens": MAX_TOKENS, "reset_ts": now})
                item = self._refill_if_needed(now, item)

                if item["tokens"] <= 0:
                    # No token – wait a bit and retry
                    time.sleep(0.05)
                    continue

                # 2️⃣ Try to atomically decrement the token count
                TABLE.update_item(
                    Key={"pk": self.pk, "sk": self.sk},
                    UpdateExpression="SET tokens = tokens - :dec",
                    ConditionExpression="tokens > :zero",
                    ExpressionAttributeValues={":dec": 1, ":zero": 0},
                )
                # Success – we own a token
                return True
            except ClientError as e:
                # ConditionalCheckFailedException means another worker took the token
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                # Small back‑off before retrying
                time.sleep(0.02)
        return False   # timed‑out
```

**How to use it inside a LangGraph node (or Lambda):**

```python
from langchain_core.runnables import RunnableLambda

rate_limiter = DistributedRateLimiter()

async def _call_oss_llm(input_: dict) -> dict:
    # Block until we have a token (or raise after timeout)
    if not rate_limiter.acquire(timeout=5):
        raise RuntimeError("Rate‑limit acquisition timed out")
    # Now we are safe to call the LLM
    return await llm_free.ainvoke(input_)

oss_llm_runnable = RunnableLambda(_call_oss_llm)
```

> **Tip:** If you run Lambdas inside a **VPC with a NAT gateway**, all Lambdas share the same public IP, so the bucket protects you from exceeding the per‑IP limit. If you have multiple NAT gateways (or use **AWS PrivateLink** to OpenRouter), you can create a separate bucket entry per NAT IP.

---

## 2️⃣ LangChain / LangGraph Wrappers

### 2.1 Rate‑limited + Retry

```python
from langchain_core.runnables import RunnableRetry
from openai import OpenAIError   # raised for 429, 5xx, timeouts

# 5 attempts, exponential back‑off (1,2,4,8,16 s)
oss_llm_with_retry = RunnableRetry(
    oss_llm_runnable,
    retry_if_exception_type=OpenAIError,
    max_attempts=5,
    wait_exponential_multiplier=1,
    wait_exponential_max=30,
)
```

### 2.2 Paid fallback (uses your $10 credits)

```python
from langchain_core.runnables import RunnableWithFallback

paid_llm = ChatOpenAI(
    model="claude-2",
    openai_api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    temperature=0.7,
    max_tokens=512,
)

oss_or_paid = RunnableWithFallback(
    primary=oss_llm_with_retry,
    fallback=paid_llm,
    # fallback only on OpenAIError (429, 5xx, timeout)
    fallback_exceptions=(OpenAIError,),
)
```

### 2.3 Usage‑tracking callback (writes a line to S3)

```python
import json
import uuid
import boto3
from datetime import datetime, timezone
from langchain.callbacks.base import BaseCallbackHandler

s3 = boto3.client("s3")
USAGE_BUCKET = "my-openrouter-usage"
USAGE_PREFIX = "daily/"

class S3UsageHandler(BaseCallbackHandler):
    """Append a JSON line to an S3 object (one line per request)."""
    def __init__(self, request_id: str):
        self.request_id = request_id

    def on_llm_end(self, response, **kwargs):
        usage = response.llm_output.get("token_usage", {})
        record = {
            "request_id": self.request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": response.llm_output.get("model_name"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "credits_spent": usage.get("credits_spent", 0),  # 0 for OSS
        }
        key = f"{USAGE_PREFIX}{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
        # Use S3's multipart upload with a small buffer (or just put_object for low volume)
        s3.put_object(
            Bucket=USAGE_BUCKET,
            Key=key,
            Body=(json.dumps(record) + "\n").encode("utf-8"),
            # If you expect many writes per second, consider using S3 Transfer Acceleration
        )
```

**Calling the LLM from a Lambda (or a LangGraph node):**

```python
import uuid

async def invoke_llm(messages: list[dict]) -> dict:
    request_id = str(uuid.uuid4())
    handler = S3UsageHandler(request_id)

    # LangChain expects a dict with a "messages" key
    payload = {"messages": messages}
    result = await oss_or_paid.ainvoke(payload, callbacks=[handler])
    return result
```

---

## 3️⃣ Wiring Everything into AWS Step Functions

### 3.1 High‑level architecture diagram

```text
+-------------------+          +-------------------+          +-------------------+
|  Step Functions   |  Map (maxConcurrency=N)  |  Lambda (LangGraph)  |
|  (orchestrates)  |  --------------------->  |  (node execution)   |
+-------------------+          +-------------------+          +-------------------+
          |                               |                         |
          |                               |   ┌─────────────────────┐
          |                               |   │ Distributed Rate    │
          |                               |   │ Limiter (DynamoDB)  │
          |                               |   └─────────────────────┘
          |                               |
          |                               |   ┌─────────────────────┐
          |                               |   │ OpenRouter OSS‑120B │
          |                               |   └─────────────────────┘
          |                               |
          |                               |   ┌─────────────────────┐
          |                               |   │ Paid fallback (Claude)│
          |                               |   └─────────────────────┘
          |                               |
          |                               |   ┌─────────────────────┐
          |                               |   │ S3 Usage Logger      │
          |                               |   └─────────────────────┘
          |                               |
          |                               v
          |                     (state persisted in S3)
          |
          v
+-------------------+
|  S3 (graph state)|
+-------------------+
```

### 3.2 Step Functions definition (YAML/JSON)

```yaml
Comment: "Parallel LangGraph execution with global rate‑limit"
StartAt: InitState
States:
  InitState:
    Type: Pass
    ResultPath: $.graph_state
    Next: ParallelMap

  ParallelMap:
    Type: Map
    ItemsPath: $.tasks               # an array of task descriptors
    MaxConcurrency: 5                # <-- control parallelism here
    Iterator:
      StartAt: CallLLM
      States:
        CallLLM:
          Type: Task
          Resource: arn:aws:lambda:us-east-1:123456789012:function:LangGraphNode
          # Pass the whole task payload + the S3 key for the graph state
          Parameters:
            payload.$: $$
            s3_key.$: $.graph_state_key
          ResultPath: $.node_result
          Next: SaveState

        SaveState:
          Type: Task
          Resource: arn:aws:lambda:us-east-1:123456789012:function:PersistState
          Parameters:
            s3_key.$: $.s3_key
            new_state.$: $.node_result.updated_state
          End: true
    End: true
```

- **`MaxConcurrency`** is the *first line of defence* – Step Functions will never launch more than this many Lambda invocations at once, keeping you well under the 10 rps limit even if each Lambda makes a single request.
- If you need **finer‑grained control** (e.g., each Lambda may issue multiple LLM calls), keep `MaxConcurrency` modest (2‑4) and rely on the **DynamoDB token bucket** for the final throttle.

### 3.3 Lambda "LangGraphNode"

```python
import json
import os
from typing import Any, Dict

# The LangGraph node code from section 2 (invoke_llm) lives here.
# Assume you have packaged the `DistributedRateLimiter`, `oss_or_paid`, etc.
from my_llm_lib import invoke_llm, load_state_from_s3, save_state_to_s3

def lambda_handler(event: Dict[str, Any], context):
    # event contains:
    #   payload: { "messages": [...], "node_id": "...", ... }
    #   s3_key: "graph-state/abcd1234.json"
    payload = event["payload"]
    s3_key = event["s3_key"]

    # Load the current graph state (if you need it)
    state = load_state_from_s3(s3_key)          # returns a dict

    # Call the LLM (rate‑limited, retry, fallback, usage logging)
    llm_response = invoke_llm(payload["messages"])

    # Update the graph state with the assistant's reply
    assistant_msg = llm_response["choices"][0]["message"]
    state["messages"].append(assistant_msg)

    # Persist the updated state back to S3
    save_state_to_s3(s3_key, state)

    return {
        "updated_state": state,
        "llm_response": llm_response,
    }
```

- **Statelessness** – the Lambda never keeps anything in memory between invocations; all coordination happens via DynamoDB (rate‑limit) and S3 (state).
- **Tracing** – you can add `print(json.dumps(...))` or CloudWatch Logs to emit the request ID, token usage, and whether a fallback was used. Those logs can be shipped to **AWS OpenTelemetry** or **CloudWatch Logs Insights** for ad-hoc queries.

---

## 4️⃣ Aggregating Usage & Credit Reports

### 4.1 Simple Athena table over the S3 JSONL logs

```sql
CREATE EXTERNAL TABLE openrouter_usage (
  request_id string,
  timestamp   string,
  model       string,
  prompt_tokens int,
  completion_tokens int,
  total_tokens int,
  credits_spent double
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://my-openrouter-usage/daily/';
```

Now you can run daily reports:

```sql
SELECT
  date(parse_iso8601(timestamp)) AS day,
  model,
  sum(total_tokens) AS tokens,
  sum(credits_spent) AS credits
FROM openrouter_usage
WHERE model = 'gpt-oss-120b'   -- free model
GROUP BY day, model
ORDER BY day DESC;
```

### 4.2 Alerting on unexpected credit usage

Create a **CloudWatch Metric Filter** on the same S3 bucket (or on the Lambda logs) that looks for `"credits_spent": > 0`. Then set an alarm that notifies you (SNS, Slack, etc.) if the daily sum exceeds a threshold (e.g., $0.10).

---

## 5️⃣ Handling Multiple NAT IPs (Optional)

If you want to **increase the effective per‑IP limit** you can:

| Option | How it works |
|--------|--------------|
| **Multiple NAT Gateways** (or **EIP‑backed NAT**) | Each Lambda will exit via a different public IP, giving you a separate 10 rps bucket per IP. You can keep a **single DynamoDB bucket** per IP (`sk = oss120b_<nat-id>`). |
| **API‑Key rotation** | OpenRouter treats each API key as a separate "client". Create a few keys (e.g., `or_key_1`, `or_key_2`) and store them in **AWS Secrets Manager**. Your Lambda picks a key at random (or round‑robin) before each request. This spreads the load across keys, but you still need the token bucket per key to avoid hitting the per‑key limit. |
| **PrivateLink / VPC Endpoint** (if OpenRouter ever offers it) | Gives you a dedicated IP range, but currently OpenRouter only exposes a public HTTPS endpoint. |

**Implementation tip:** add the NAT‑ID (or API‑key name) to the DynamoDB PK:

```python
class DistributedRateLimiter:
    def __init__(self, key_name: str):
        self.pk = "rateLimiter"
        self.sk = f"oss120b_{key_name}"   # e.g. oss120b_key1
```

Then each Lambda picks a key at random and uses the corresponding rate‑limit bucket.

---

## Summary

For AWS Step Functions with parallel LangGraph execution and S3-persisted state:

1. **Use DynamoDB as a distributed token bucket** for global rate‑limiting across all workers
2. **Wrap your LLM calls** with `RunnableRetry` + `RunnableWithFallback` from LangChain
3. **Log usage to S3** via a callback handler for Athena queries and alerting
4. **Control parallelism** via Step Functions `MaxConcurrency` as the first line of defense
5. **Scale further** by using multiple NAT IPs or rotating API keys

This pattern gives you production-scale LLM inference on the free OSS-120B model while staying within OpenRouter's limits and maintaining full observability.

