# PRD: Phase 2 — Ask your library

**Status:** v0.2, decisions agreed (2026-10-22) · **Builds on:** Phase 1 (transcripts, search, summaries) and Phase 2.5 (local transcripts, slides, local AI)

Phases 1 and 2.5 made every video *readable*: transcripts (captions or local), slides and code on screen, chapters, AI summaries and your notes. Phase 2 lets you **ask questions across all of it** and get an answer that **cites the exact videos and moments**, so you can check it or jump straight there.

> "Which videos explain LoRA vs QLoRA, and where?" → a short answer with citations like *LLM Fine-Tuning Explained @ 4:14* and *Graph RAG @ 1:01:02*, each opening the video at that second.

---

## 1. Goals

1. **Answers grounded in your videos.** Every claim cites a video and a timestamp. If the library doesn't cover something, the answer says so instead of making it up.
2. **Find by meaning, not just words.** "Making models smaller" should find "quantization"; a Hindi explanation should answer an English question.
3. **Local first.** It works fully offline with Ollama. Cloud models stay optional, per task, as in Phase 1.
4. **Fast enough to feel like search.** Retrieval in under 1 s; answers in seconds (cloud) or tens of seconds (local 4B model).

## 2. Non-goals

- Web search or knowledge beyond your library (answers say "not covered in your videos").
- Acting on anything (changing statuses or playlists) from a chat answer.
- Fine-tuning or training models.

## 3. How it works

1. **Pieces to search:** reuse the Phase 1 search pieces (`search_docs`): ~30 s transcript blocks (from the *active* transcript), slides, chapters, descriptions, AI summaries and notes, each with its video and second.
2. **Retrieval (hybrid):**
   - *Keyword*: the existing FTS5 trigram index.
   - *Meaning*: embeddings from a small local model (recommended `qwen3-embedding:0.6b`, 0.64 GB, multilingual) stored in SQLite and compared with cosine similarity. Brute force in numpy is fine up to ~100k pieces.
   - Results are merged (reciprocal-rank fusion), with filters for category, playlist and "unwatched only". The top pieces are trimmed to the answer model's context.
3. **Answer:** a new AI task, **Questions and answers**, uses the chosen provider (Phase 1 per-task routing; local by default). The prompt gets the numbered pieces and must cite them as `[n]`; citations are checked in code against the pieces actually sent (invalid ones are dropped). Answers are written in the user's preferred language.
4. **Follow-ups:** a short conversation. Follow-up questions are rewritten into standalone questions before retrieval, so "and where does he show the code?" works.
5. **Keeping the index fresh:** an incremental background job (kind `embed`) embeds new or changed pieces after syncs, details fetches, transcriptions and slide reads. Embeddings are tied to the model that made them, and switching models re-embeds.

## 4. Features

### A1 — Ask page
- A question box and the answer, with citation chips (*video title @ time*). A chip opens the video panel at that second.
- A "Sources" list underneath: the retrieved pieces with snippets, so you can see what the answer was based on.
- Filters: category, playlist, include watched / skipped.

### A2 — Ask from a video
- In the video panel: "Ask about this video". Same flow, limited to that video's pieces.

### A3 — Semantic search in the Search page
- An option on the Search page to blend meaning-based results with keyword results (same retrieval as A1, no AI answer).

### A4 — Setup and status
- On the AI page: an embedding model choice (local) with **Download / Use**, the index status ("N of M pieces embedded"), and **Rebuild**.
- `scripts/setup.sh --local-ai` also offers the embedding model.

## 5. Data model (additions)

- `embeddings(doc_id → search_docs.id, model, dim, vector BLOB float32)`, with a "stale" flag when the piece's text changes.
- `ask_history` (only if decided in §7): question, answer, citations JSON, filters, model, created_at.

## 6. Build order (proposed)

1. **Spike:** embed the current library with `qwen3-embedding:0.6b` and measure speed, size and retrieval quality on 15–20 hand-written questions (keyword vs meaning vs hybrid). Check that answer citations from the local 4B model are accurate.
2. Embedding index plus incremental `embed` job, and hybrid retrieval (`retrieve()`) with tests.
3. Ask page (A1): answers with checked citations and sources.
4. Follow-ups, Ask about this video (A2) and semantic search (A3).
5. Setup and status (A4), docs and setup script.

Each step ships with tests and keeps the dashboard working.

## 7. Decisions (2026-10-22)

1. **Retrieval: hybrid.** Keyword (FTS5) plus a local embedding model (`qwen3-embedding:0.6b`, ~0.64 GB) for matching by meaning and across languages.
2. **History: saved.** Questions, answers, citations and filters are kept in `ask_history`; past answers can be re-opened, searched and exported to notes.
3. **Answer model: same as ratings by default.** The new *Questions and answers* task follows the ratings provider unless the user picks another on the AI page.
