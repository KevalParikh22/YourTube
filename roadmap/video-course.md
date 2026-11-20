# A 4–5 hour video becomes a weekly course

**Status (2026-11-20):** done except reminders (step 4).
- `summarizer.split_parts` cuts at the chapter start nearest each 30 min mark, else at the longest pause between caption lines within 20–40 min (a cheap stand-in for asking the LLM where topics change). `summarize_parts` makes one call per part: title, summary, 3–6 timed points, 1–3 actions. `run_course` reuses parts already made by a long summary.
- `courses` table (`storage.save_parts`, `get_course`, `set_course_pace`, `set_course_progress`, `list_courses`): parts, parts per week (NULL: parts only, no course), progress (`p<i>` part watched, `a<i>.<j>` action done). Weeks are computed from the pace, so changing it never loses progress; making the parts again resets it.
- API: `GET/POST /api/course`, `POST /api/course/progress`, `GET /api/course/markdown` (`exporter.course_markdown`). A new course is a `course` task in the Summaries AI's lane and goes through the night queue.
- UI: video panel → **Make a weekly course** (videos over 40 min); `#/course?id=` page with weeks, **▶ Watch** from the part's start, ticks, pace, Export Markdown, End course; Home → **Courses**.

**Problem:** Long videos (courses, workshops, podcasts) don't get watched. Core idea of the project, not built yet: turn one long video into a plan you can actually finish, with actions.

**Plan**
1. Split: use chapters when present, else cut the transcript into 20–40 min parts at topic changes (LLM over chunked transcript).
2. For each part: summary, key points, 1–3 concrete actions/exercises, and the time range to watch.
3. Schedule: "N parts per week" → a course page with weeks, parts, watch links that seek to the part, a checkbox per action, progress stored per part (extend video status to parts).
4. Reminders: optional, later (needs notifications).
5. Export the course as Markdown (`exporter.py`).

Depends on chunked summarising from [off-peak-queue.md](off-peak-queue.md) and benefits from [token-tracking.md](token-tracking.md) for cost.

**Done when:** a 4 h video produces a 3–4 week course page with parts, actions and progress that survives reloads.
