# A 4–5 hour video becomes a weekly course

**Status (2026-11-21):** done except reminders (step 4); ideas 1 (quiz per part) and 2 (part 1 ready first, with Continue after a stop; parts not written yet are stored as `{"start", "end", "pending": true}`) below are done too.
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

## Next: ideas from courseit.xyz (checked on its site, 2026-11-20)

CourseIt turns a YouTube video or playlist (up to 20 h) into lessons with notes and a quiz, split into days by a daily time budget (30–120 min). What its site confirms, and what would help here, most useful first:

| # | Idea | Why it helps finish a long video | Size |
|---|------|----------------------------------|------|
| 1 **Done** | **Quiz per part with "replay the moment"**: 2–3 questions in the part prompt, each with its time; a wrong answer seeks to that time (reuse `play-at`) | Recall instead of passive watching; fixing a gap costs one click | M |
| 2 **Done** | **Part 1 ready first**: `summarize_parts` saves after each part, the course page shows parts as they arrive | Start while the rest is made (a 4 h course otherwise waits for every call, or for the night) | S |
| 3 | **Daily pace in minutes**: "N min a day" next to "parts a week", with a finish date from the parts' lengths | Fits a daily habit; a date gives a goal | S |
| 4 | **Placement check**: a few quiz questions up front; parts you already know are ticked | Skips the intro parts that make people drop long tutorials | M (needs 1) |
| 5 | **Roadmap**: chain several courses (or a playlist) into one plan | A playlist course is the next step after one long video | L |

CourseIt does not offer export, reminders or offline/local AI; YourTube already has Markdown export and runs locally. Its splitting method is not described on the site, so splitting at topic changes with the LLM stays an open idea (step 1 of the plan uses chapters and pauses).
