# A 4–5 hour video becomes a weekly course

**Problem:** Long videos (courses, workshops, podcasts) don't get watched. Core idea of the project, not built yet: turn one long video into a plan you can actually finish, with actions.

**Plan**
1. Split: use chapters when present, else cut the transcript into 20–40 min parts at topic changes (LLM over chunked transcript).
2. For each part: summary, key points, 1–3 concrete actions/exercises, and the time range to watch.
3. Schedule: "N parts per week" → a course page with weeks, parts, watch links that seek to the part, a checkbox per action, progress stored per part (extend video status to parts).
4. Reminders: optional, later (needs notifications).
5. Export the course as Markdown (`exporter.py`).

Depends on chunked summarising from [off-peak-queue.md](off-peak-queue.md) and benefits from [token-tracking.md](token-tracking.md) for cost.

**Done when:** a 4 h video produces a 3–4 week course page with parts, actions and progress that survives reloads.
