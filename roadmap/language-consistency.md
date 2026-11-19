# One output language for every video

**Problem (working assumption, confirm with the reporter):** with videos in several languages, AI output comes back mixed: summaries follow Settings → Language, but ratings' reasons, key points, knowledge pages or category suggestions sometimes come back in the video's own language; and changing the language leaves old summaries in the old one.

**Plan**
1. Pass the same `language` setting into every prompt that writes text for the user (`rater.py`, `knowledge.py`, `asker.py`, `ai_classifier.py`), as `summarizer.py:60` already does.
2. Store the language on each summary (already returned at `summarizer.py:153`) and on ratings; show "Summary in Hindi · Redo in English" when it differs from the current setting, and add a bulk "Redo in current language".
3. Keep search language-neutral: the meaning index already matches across languages; no change.

**Done when:** a library with Hindi and English videos and Language = English produces English summaries, reasons, key points and knowledge pages.
