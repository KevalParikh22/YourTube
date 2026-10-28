# Plan: Sidebar navigation, floating Ask, safer category adding

**Goal:** The dashboard looks like a professional app: a left **sidebar** holds the sections, a **settings gear** sits in the top bar, and **Ask** becomes a floating chat button available on every page. Categories you add yourself open as an editable row and are safe from AI proposals.
**Branch:** feat/nav-redesign
**Out of scope:** new features; changes to the API (everything here is in `web/index.html`, except T3's one-line protection on the server side if needed); restyling page content beyond what the new frame needs.

## Decisions and suggestions (2026-10-28, user request; confirm open points before T1)
- **Sidebar instead of top tabs.** Grouped sections with icons; it collapses to icons only (remembered), and becomes a slide-in menu on phones.
- **Settings via a gear icon** in the top bar (right), not a sidebar item.
- **Ask as a floating chat button** (bottom-right) that opens a chat panel. Suggestion: keep the full Ask page as the panel's **Expand** view, for long conversations and history. The panel is for quick questions; it knows the open video ("Ask about this video" opens the panel scoped to it).
- **Categories you add are protected from AI** automatically (you can untick it), and are added through an inline row, not a browser pop-up.

## Design notes

### A. Frame: top bar + sidebar (T1)
- Layout: CSS grid `grid-template-columns: var(--side, 232px) minmax(0, 1fr)`; `.side` is fixed full-height (`position: sticky; top: 0; height: 100vh; overflow-y: auto`), the page content scrolls on its own. Collapsed: `--side: 64px`, labels hidden, `title` on each item. Remember the state in `localStorage` key `ytkb.side` (try/catch like `store`).
- Sidebar contents, top to bottom:
  1. Logo "YourTube" (collapsed: a "Y" mark);
  2. the **library picker** (moved from the top bar; collapsed: hidden);
  3. groups with a small uppercase heading: **Watch**: Library, Triage, Plan; **Learn**: Knowledge, Search; **Manage**: Playlists, Categories;
  4. bottom: the collapse toggle.
  Each item is an `<a href="#/...">` with an inline 18 px SVG icon (stroke `currentColor`, 1.6 px; simple shapes: grid, list-check, calendar, book, search, list, tag) and its label. The active item uses the existing `.on` style (accent on the left edge, `--sunken` background).
- Top bar (thin, 52 px, inside the content column): page title (from the route) on the left; on the right: the task pill (`#jobs`), the search icon (`#gsearch-btn`), and the **settings gear** (`<a href="#/settings" aria-label="Settings">` with a gear SVG; active when on any Settings page).
- Remove the old tab bar (`TABS`, `#tabs`, `renderBar` tab code) and the old header layout; keep the theme logic. Keep every route working (`#/`, `#/plan`, `#/triage`, `#/knowledge`, `#/search`, `#/sources`, `#/categories`, `#/settings`, `#/ai`, `#/setup`, `#/ask`).
- Phones (`max-width: 760px`): the sidebar is hidden; a hamburger button in the top bar opens it as an overlay (with a scrim), closing on navigation or Escape. No horizontal scroll at 375 px.
- The Library page's own filter rail (Show / Categories / Playlists) stays inside the content, as now.

### B. Floating Ask (T2)
- A round 52 px button fixed at bottom-right (`right: 20px; bottom: 20px; z-index` above content, below the video drawer's scrim when the drawer is open), with a chat icon, `aria-label="Ask your library"`. It's hidden on the full Ask page (`#/ask`).
- Click: a panel slides up (desktop 400×600, anchored bottom-right; phones: full screen) with:
  - a header showing "Ask" and the scope chip ("This library" / "Only: <video title>" with ✕), plus buttons **New**, **Expand** (goes to `#/ask?id=<thread>` or `#/ask`) and **Close**;
  - the conversation, reusing `askTurn()`. Sources are collapsed by default in the panel; citation chips call `playAt` and open the drawer;
  - the input at the bottom (Enter sends, follow-ups as today through `runAsk`).
  It shares `S.ask` with the full page, so a conversation continues in either place. Escape closes it; focus moves into the panel on open and back to the button on close.
- "Ask about this video" in the video drawer opens the **panel** scoped to that video, instead of navigating to `#/ask`.
- Remove Ask from the sidebar (the floating button replaces it); the full page stays reachable through Expand.

### C. Categories you add (T3)
- **Add category** shows an inline row at the top of the list (a name input with focus, a keywords textarea, and Save / Cancel) instead of `prompt()`. Save calls the existing taxonomy save, then marks the new name protected through the existing `/api/presets {name, preset: true}`. The row then becomes a normal, fully editable category row.
- The proposal view already leaves protected categories untouched and never lists them under "left out", so user-added categories can no longer be offered for removal.
- Empty name or a duplicate name → an inline error, nothing saved.

## Tasks

### T1: Sidebar navigation and top bar with a settings gear
- **worker:** codex
- **status:** todo
- **files:** `web/index.html`
- **steps:** Design notes A. Grep for `TABS`, `renderBar`, `#tabs`, `.bar`, `#libpick`, `#jobs`, `#gsearch-btn` and change only the frame (CSS + header markup + `renderBar`).
- **acceptance:** [ ] every route reachable from the sidebar or the gear; [ ] the active item is highlighted; [ ] collapse works and is remembered; [ ] phone overlay works; [ ] no horizontal scroll at 375 px; [ ] light and dark themes.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T2: Floating Ask chat panel
- **worker:** codex
- **status:** todo
- **depends on:** T1
- **files:** `web/index.html`
- **steps:** Design notes B; reuse `askTurn`, `runAsk`, `S.ask` and `askAbout`.
- **acceptance:** [ ] ask and follow up from any page; [ ] citations open the video at the moment; [ ] Expand continues the same conversation on the full page; [ ] "Ask about this video" opens the panel scoped to that video; [ ] keyboard and Escape work; [ ] AI text escaped.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T3: Inline Add category, protected by default
- **worker:** agy
- **status:** todo
- **depends on:** T1
- **files:** `web/index.html`
- **steps:** Design notes C.
- **acceptance:** [ ] no browser pop-up; [ ] the new category is saved, protected and editable; [ ] a later AI proposal doesn't list it for removal.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T4: Check, docs and screenshots
- **worker:** claude
- **status:** todo
- **depends on:** T1–T3
- **files:** `docs/SETUP.md` (navigation wording: "Settings → …" becomes the gear), `docs/MANUAL-TESTS.md` (session S8: navigation, Ask panel), `CLAUDE.md` (routes and frame)
- **verify:** read-through and a check of the dashboard at desktop and phone width
