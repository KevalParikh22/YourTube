# Token use tracking and cutting it down

**Problem:** Cloud runs show an estimate before they start (`llm.estimate_cost`), but nothing records what was actually used, so you can't see spend per task, per provider or per day, or which tasks waste tokens.

**Plan**
1. Record usage: every provider call in `llm.py` returns its usage numbers (Claude `usage`, Gemini `usageMetadata`, OpenAI `usage`). Save one row per call in a new `ai_usage` table (time, task, provider, model, video_id, tokens_in, tokens_out, cost) in `storage.py`.
2. Show it: Settings → AI gets a "Usage" block: today / this month, by task and provider, plus actual vs. estimated cost after each run in the task log.
3. Cut it down:
   - Don't resend what's done: skip already rated/summarised videos (fix #5 in the README).
   - Send less transcript: cap per task (ratings need the first N minutes + chapters, not the whole text); reuse the summary as input for ratings when one exists.
   - Cache prompts where the provider supports it (Claude prompt caching for the shared system prompt).
   - Optional daily token/cost cap per provider; when reached, remaining work waits for [off-peak-queue.md](off-peak-queue.md) or the next day.

**Done when:** every cloud call is recorded, the totals match the provider's console within a few percent, and a cap stops a batch cleanly.
