# Plan: Navigation revamp, floating Ask, Settings with its own sidebar

**Goal:**
- The top navigation keeps the main pages: Library, Knowledge, Plan, Triage, Playlists, Categories.
- **Ask** becomes a floating chat button that opens a pop-up, with a link to the full page.
- **Search** (bigger) and **Settings** are icons on the right of the top bar, plus a **Help** icon.
- **Settings** gets a left sidebar of clearly separated sections, including Cloud AI and Local AI as separate ones.
- **Setup and help** moves to its own page.
- **Keyboard shortcuts** and accessibility work across the site.
- "About you" validates the interests text and offers a **language dropdown**.
- Categories you add yourself are editable and protected from AI removal.

**Branch:** feat/nav-redesign
**Out of scope:** a sidebar for the main navigation (the user keeps the top bar); new AI features; API changes beyond language validation.

## Decisions (2026-10-28, from the user)
- Main navigation stays a **top bar**. Ask, Settings and Setup leave it.
- Ask: a **floating icon** opens a chat pop-up, and the pop-up can open the full Ask page.
- Top-right order: **Search** (larger icon), **Settings** (gear), **Help** (?).
- **Settings uses a sidebar** for its sections. It must **not** contain Setup and help.
- Cloud AI and local AI are **separate sections**; using both at once (per task) keeps working.
- Interests ("About you") get **validation**; the response language is a **dropdown of supported languages**.
- Option descriptions (such as "suggest after sync") are kept or improved where unclear.

## Design notes

### A. Top bar (T1)
- Nav items: Library `#/`, Knowledge `#/knowledge`, Plan `#/plan`, Triage `#/triage`, Playlists `#/sources`, Categories `#/categories`. `TABS` holds only these.
- Right side, in order: the task pill (`#jobs`); **Search**: a 36×36 icon button with a 20 px magnifier SVG (replaces the small "⌕" glyph), `aria-label="Search (/)"`, `title="Search  /"`; **Settings**: a 36×36 gear SVG, `href="#/settings"`, highlighted on any Settings route; **Help**: a 36×36 "?" circle, `href="#/help"`.
- The library picker stays next to the logo.
- Phones: the nav scrolls sideways as now; the three icons stay visible.

### B. Floating Ask pop-up (T2)
- A round 56 px button, bottom-right (`right: 20px; bottom: 20px`), with a chat-bubble SVG, `aria-label="Ask your library (A)"`. Hidden on the full `#/ask` page. Its z-index sits below the video drawer's scrim.
- Click (or the `A` key) opens a panel (desktop 400×600 anchored bottom-right; phones: full screen) with:
  - a header: "Ask your library", the scope chip ("This library" or "Only: <video title>" with ✕), and buttons **New**, **Open full page** (→ `#/ask?id=<current thread>` or `#/ask`) and **Close**;
  - the conversation, reusing `askTurn()` with sources collapsed; citation chips call `playAt`;
  - the input (Enter sends; follow-ups through `runAsk`; disabled while busy).
- It shares `S.ask` with the full page. Escape closes it; focus goes into the input on open and back to the button on close.
- The video drawer's "Ask about this video" opens the **panel** scoped to that video.

### C. Settings with a sidebar; Help page (T3)
- Routes: `#/settings/<section>` (`#/settings` = General). The old `#/ai` and `#/setup` links redirect to `#/settings/cloud` and `#/help`.
- Layout: a left sidebar (200 px, sticky) listing the sections; content on the right. Phones: the sidebar becomes a horizontal chip row.
- Sections and contents (move the existing cards; don't rewrite their logic):
  1. **General**: default speed, theme.
  2. **About you**: interests (per library) and summary language (T4 adds validation and the dropdown).
  3. **AI by task** ("Which AI does what"), plus "Local only".
  4. **Cloud AI**: the providers that need an API key (Claude, Gemini, OpenAI-compatible), with keys and models.
  5. **Local AI**: the computer check, Ollama and LM Studio providers, models, speech-to-text, vision model, slide quality, meaning-search index.
  6. **Sync and details**: "Fetch details for new videos after each sync", "Use descriptions in search and AI", "Suggest categories for new videos after each sync" (moved from About you), each with a clear one-line description and ⓘ. The Playlists page keeps a small "Sync settings" link here.
  7. **Scoring**: "How scores are calculated" (the rating formula editor).
- **Help page** `#/help`: the current Setup content (scripts, downloads, guide) plus the keyboard shortcut list (T5). Settings no longer shows Setup and help.

### D. About you validation and language dropdown (T4)
- Interests: optional, but if filled in, at least 3 words and at most 2,000 characters. Show a live counter ("123 / 2000"). Saving invalid text shows an inline error under the field (not only a toast), and nothing is saved.
- Language: a `<select>` from one list, `llm.LANGUAGES` = English, Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Punjabi, Urdu, Spanish, French, German, Portuguese, Italian, Dutch, Russian, Japanese, Korean, Chinese, Arabic, Turkish, Indonesian. `public_settings` exposes it as `languages`. The client `LANGUAGES` constant is removed; the client uses `S.data.ai.languages`.
- Server (`/api/ai/settings`): reject a language not in `llm.LANGUAGES` (400 "Choose a supported language"); reject interests over 2,000 characters (400). A saved value outside the list (an older free-text language) still shows, selected, as "<value> (custom)", so nothing is silently changed.
- Tests: invalid language → 400; a valid one saves; interests length.

### E. Keyboard shortcuts and accessibility (T5)
- Shortcuts (ignored while typing in an input, textarea or select, or while a modifier is held):
  - `/` search;
  - `A` open Ask;
  - `?` shortcut help overlay;
  - `G` then `L` Library, `K` Knowledge, `P` Plan, `T` Triage, `Y` Playlists, `C` Categories, `S` Settings, `H` Help (a 1.5 s window after `G`);
  - `Esc` closes the overlay, Ask panel or drawer;
  - `J` / `K` move between videos while the drawer is open (exists).
- The overlay is a modal dialog (`role="dialog"`, `aria-modal`, focus trapped, Escape closes) listing the shortcuts. The same table appears on the Help page.
- Accessibility: a "Skip to content" link as the first focusable element; visible focus on every control; `aria-current="page"` on the active nav item; icon buttons have `aria-label`; the Ask panel and the overlay restore focus when closed.

### F. Categories you add (T6)
- **Add category** opens an inline row at the top of the list (name input focused, keywords textarea, Save / Cancel) instead of `prompt()`. Save stores it through the existing taxonomy save, then marks it protected through `/api/presets {name, preset: true}`. It is then a normal, fully editable row, which an AI proposal never lists for removal. An empty or duplicate name shows an inline error.

## Tasks

### T1: Top bar: main pages only; Search, Settings and Help icons
- **worker:** codex
- **status:** done
- **files:** `web/index.html`
- **steps:** Design notes A.
- **acceptance:** [ ] nav shows the six pages; [ ] icons in order, search larger, with labels; [ ] active states; [ ] no horizontal page scroll at 375 px.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T2: Floating Ask pop-up
- **worker:** codex
- **status:** done
- **depends on:** T1
- **files:** `web/index.html`
- **steps:** Design notes B.
- **acceptance:** [ ] ask and follow up from any page; [ ] citations play the moment; [ ] Open full page continues the same conversation; [ ] "Ask about this video" opens the panel scoped to it; [ ] focus and Escape work; [ ] AI text escaped.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T3: Settings sidebar sections; separate Help page
- **worker:** codex
- **status:** todo
- **depends on:** T1
- **files:** `web/index.html`
- **steps:** Design notes C (move cards, keep their handlers).
- **acceptance:** [ ] seven sections reachable from the sidebar; [ ] Cloud AI and Local AI separate, and per-task routing still mixes them; [ ] old `#/ai` and `#/setup` links still work; [ ] Help is not inside Settings.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T4: About you validation and language dropdown
- **worker:** agy
- **status:** todo
- **depends on:** T3
- **files:** `llm.py`, `dashboard.py`, `web/index.html`, `tests/test_dashboard.py`
- **steps:** Design notes D.
- **acceptance:** [ ] the validation and dropdown work; [ ] server rejects bad input; [ ] tests.
- **verify:** `.venv/bin/python -m unittest`

### T5: Keyboard shortcuts and accessibility
- **worker:** codex
- **status:** todo
- **depends on:** T2, T3
- **files:** `web/index.html`
- **steps:** Design notes E.
- **acceptance:** [ ] every shortcut works and none fires while typing; [ ] the overlay and Help list match; [ ] skip link and focus handling.
- **verify:** `.venv/bin/python -m unittest` and a manual keyboard-only check

### T6: Inline Add category, protected by default
- **worker:** agy
- **status:** todo
- **depends on:** T1
- **files:** `web/index.html`
- **steps:** Design notes F.
- **acceptance:** [ ] no pop-up; [ ] saved, protected, editable; [ ] never offered for removal.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T7: Check, docs, manual tests
- **worker:** claude
- **status:** todo
- **depends on:** T1–T6
- **files:** `docs/SETUP.md` (navigation wording, shortcuts), `docs/MANUAL-TESTS.md` (session S8), `CLAUDE.md` (routes)
- **verify:** dashboard check at desktop and phone width; read-through
