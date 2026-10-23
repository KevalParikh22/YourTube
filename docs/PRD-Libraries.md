# PRD: Separate libraries

**Status:** built (2026-10-23)

One backlog mixes very different things: tech tutorials, health, personal growth. Their categories, goals and ratings don't belong together. **Libraries** split them: each library has its own playlists, categories, interests, time left, triage and answers.

## Decisions (2026-10-23)

1. **Assignment: per playlist.** Each playlist, and the *Added one by one* source, belongs to one library, chosen when it is added and changeable later. Videos follow their playlist. A video saved in playlists of two libraries appears in both, with one status (and one category, which belongs to the library of its first playlist).
2. **Categories: separate per library.** Each library has its own category list, keywords, presets ("Keep"), notes and AI proposal. A new library starts with no categories (everything is *General / Other*) until you add some or use *Propose categories with AI*.
3. **Switching: a library picker in the header.** Library, Plan, Triage, Search, Ask and Categories show the current library. *All libraries* shows everything (categories can't be edited there).
4. **Interests: per library.** Ratings judge a video against its own library's interests, so a health video isn't marked down for not being technical. The language for summaries stays global.

## How it is stored

- `libraries(id, name, created_at)`; `playlists.library_id` (default 1).
- Library 1 ("My library") keeps the existing places: `taxonomy.json`, and `presets`, `category_notes`, `proposal`, `interests` at the top of `settings.json`. Nothing moves, so older data and tests keep working.
- Other libraries: `taxonomy-<id>.json`, and `settings.json["libraries"]["<id>"]` with the same four keys.
- A video's library is the library of its first playlist that still contains it (else its first playlist).

## Build steps

1. Storage and settings: the table, playlist assignment, per-library taxonomy and settings, video → library. Sync, keyword sorting, AI sorting, proposals and ratings use the video's library.
2. API: `/api/state?library=` returns that library's categories, presets, notes, proposal and interests; POSTs that change categories take `library`; libraries can be created, renamed, deleted (only when empty) and playlists moved.
3. Dashboard: header switcher (remembered per browser), library-scoped pages, library column and picker on the Playlists page, interests per library in Settings.
4. Docs and tests.

All four steps are done. Checked on a copy of the real library: moving one playlist into a new *Health* library split it 87 / 232 videos (2 in both), re-sorted the moved videos with Health's (empty) categories, and *Propose categories with AI* for Health suggested 8 categories from Health's videos only (Mental performance, Fitness, Relationships, Wealth, Spirituality…), leaving My library's proposal untouched.

Also: moving a playlist re-sorts its videos with the new library's keywords (categories set by hand or accepted from AI stay); each library has its own *Added one by one* source (`_single`, `_single-<id>`).
