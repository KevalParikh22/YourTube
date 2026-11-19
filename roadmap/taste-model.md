# Recommendations from how you choose and drop videos

**Problem:** Ratings judge videos against the interests you write down. Real taste shows in behaviour: what you open, finish, skip, drop half-way, rewatch, or leave in the queue for months.

**Plan**
1. Collect signals (local only, never uploaded): opened, watch progress, finished, skipped, dropped at %, time in queue before action, notes written, rating overridden by you. Most exist already in video status and progress; add an events table for the rest.
2. Features per video: channel, length, category, topic embedding (from the meaning index), language, upload age, AI verdict.
3. Model: start simple and explainable — a per-user logistic regression / gradient boosting on those features predicting "finish vs. drop", retrained nightly. Show its score next to the AI verdict and blend them.
4. Explain: "Ranked up because you finish most 20–40 min videos from this channel".
5. Later: use the same score to pick what [channel-follow.md](channel-follow.md) summarises automatically.

Risk: needs a few hundred decisions before it beats the AI verdict; keep the AI verdict as the fallback and show confidence.

**Done when:** on a held-out month of your history, the blended score ranks finished videos above dropped ones better than the AI verdict alone.
