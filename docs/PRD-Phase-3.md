# PRD: Phase 3 — The brain

**Status:** built (2026-10-28)

Before Phase 3, a video only had notes once it was rated, so 10 of 313 videos had any. Phase 3 makes the summary its own step, richer than YouTube's, and adds learning across videos.

## Decisions (2026-10-27)
1. Summaries are made **only when the user clicks Summarise** (per video or a selection). Ratings stay a separate, personal step.
2. Beyond YouTube's summary: **key claims and quotes**, **related moments in other videos**, **check-yourself questions**.
3. Learn more: **from the library + YouTube search links** for gaps.
4. Knowledge base: **topic pages**, one per category of a library, every point cited.

## What was built
- **Summary** (TL;DR, minutes with timestamps, claims and quotes, questions with answers and times, takeaways, learn more). Related moments are found by meaning in code, not by the model, so they always point to real passages.
- **Knowledge pages**: one per category, from its summarised videos; points without a valid citation are dropped; pages turn "stale" when a summary is added or redone.
- Descriptions collapsed in the video panel (`show_descriptions` setting); timestamps shown once; a library without interests uses the first library's.

## Results on the real library (local qwen3.5:4b, RTX 3050)
- Summaries: about **100 s per video**; concrete minutes, claims such as "LoRA trains roughly 100 million parameters instead of 70 billion", good questions with answer times.
- Related moments at cosine ≥ 0.6: mostly relevant; 0.55 let loosely related news clips through.
- Knowledge page from 3 videos: 101 s; every citation valid. With the 8k local context the oldest videos are shortened, so pages lean to the newest videos; a cloud model or a larger context gives more balance.

## Not built
- Background summarising of the whole library (the user chose on-click only).
- Web knowledge beyond the library (only YouTube search links).
