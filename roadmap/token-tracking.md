# Token use tracking and cutting it down

**Problem:** Cloud runs show an estimate before they start (`llm.estimate_cost`), but nothing records what was actually used, so you can't see spend per task, per provider or per day, or which tasks waste tokens.

**Status (2026-11-20):** steps 1 and 2 are done; step 3 is done except Claude prompt caching, rating from a summary and cached-token pricing.
- Every cloud call is saved in `ai_usage` by `llm.record_usage`: text AI (`make_completer`), slide reading (`see`), embeddings (`embedder.py`; Gemini reports no tokens, so they are estimated and marked) and cloud transcription (`cloud_asr._record`, priced per audio hour). Models on this computer are not saved.
- Each task's log ends with "AI use: N cloud calls, X tokens in, Y out, about $Z" (totals include helper threads).
- Settings → Cloud AI → **Usage**: today and the last 30 days per task and model (`GET /api/ai/usage`).
- **Daily budget** (step 3, done): Settings → Cloud AI → **Daily budget (USD)** sets a USD cap per local calendar day for all cloud AI, including Claude (empty or 0 = no cap; a number up to 1000, step 0.5). `llm.check_cap` runs before every Claude call as well as every call through `llm._open`. When today's recorded cost reaches the cap, cloud calls raise `QuotaError` and the batch stops cleanly; local models are not affected. It resets at local midnight.
- **Ratings send less transcript** (step 3, done): `rater.RATE_CHAR_BUDGET` caps a rating prompt at 48,000 characters (about 12k tokens, a ~50 min talk in full), and is the default budget for `build_prompt`, `rate_video`, `estimate` and `run_rate`. `rater.fit_transcript` samples blocks evenly across the whole video when the transcript is longer, and the prompt marks it "evenly sampled excerpts"; slides get at most a quarter of the room. Local models with a smaller context keep their smaller budget. Summaries still use the full budget.
- **Prompt order for caching** (step 3, partly done): the fixed instructions and JSON shape now come first, and video-specific text last. In ratings (`rater.build_prompt`): instructions, then today's date, interests and categories, then `Now the video to rate:` with the video, chapters, transcript and slides. In summaries (`summarizer.build_prompt` and `PART_PROMPT`): instructions, then `Now the video:` with the video, transcript or part text, slides and the related-videos list. Gemini and OpenAI can reuse the fixed prefix; Claude prompt caching is not done.
- Not done: a video_id column, comparing the actual use with the estimate shown before a run, prices for models missing from `llm.PRICES` (they show "price unknown"), Claude prompt caching, rating from a summary, and cached-token pricing.

**Plan**
1. Record usage: every provider call in `llm.py` returns its usage numbers (Claude `usage`, Gemini `usageMetadata`, OpenAI `usage`). Save one row per call in a new `ai_usage` table (time, task, provider, model, video_id, tokens_in, tokens_out, cost) in `storage.py`.
2. Show it: Settings → AI gets a "Usage" block: today / this month, by task and provider, plus actual vs. estimated cost after each run in the task log.
3. Cut it down:
   - Don't resend what's done: skip already rated/summarised videos (**done**: fix #5 in the README).
   - Send less transcript: **done for ratings** with a 48,000-character cap and even sampling across the video; reuse the summary as input for ratings when one exists (not done).
   - Cache prompts where the provider supports it: fixed instructions first, video-specific text last (**done** for Gemini/OpenAI prefixes); Claude prompt caching for the shared system prompt (not done).
   - Optional daily token/cost cap (**done**: one `daily_cap` for all cloud AI, including Claude, not per provider; when reached, the batch stops cleanly and work waits for the next day).

**Done when:** every cloud call is recorded, the totals match the provider's console within a few percent, and a cap stops a batch cleanly.
