# One output language for every video

**Problem (working assumption, confirm with the reporter):** with videos in several languages, AI output comes back mixed: summaries follow Settings → Language, but ratings' reasons, key points, knowledge pages or category suggestions sometimes come back in the video's own language; and changing the language leaves old summaries in the old one.

**Status (2026-11-22):** done (steps 1–3).
- One rule, `llm.language_rule(language)`, in every prompt that writes for the user: ratings, summaries, course parts, knowledge pages, Ask answers. It says to write in the chosen language even when the video, transcript or excerpts are in another, and to keep names, code and on-screen text.
- Summaries and ratings already store their language. `storage.other_language` lists the ones in another language (`/api/state` → `other_language`); the video panel shows "Summary in X · Redo in Y" / "Rating in X · Redo in Y", and Settings → About you offers **Redo in Y** for all of them (cost confirm first). Rows saved before the language was stored are left out.
- Not done: category names and keywords (keywords must match the videos' own titles), knowledge pages are not flagged when made in an old language.

**Plan**
1. Pass the same `language` setting into every prompt that writes text for the user (`rater.py`, `knowledge.py`, `asker.py`, `ai_classifier.py`), as `summarizer.py:60` already does.
2. Store the language on each summary (already returned at `summarizer.py:153`) and on ratings; show "Summary in Hindi · Redo in English" when it differs from the current setting, and add a bulk "Redo in current language".
3. Keep search language-neutral: the meaning index already matches across languages; no change.

**Done when:** a library with Hindi and English videos and Language = English produces English summaries, reasons, key points and knowledge pages.
