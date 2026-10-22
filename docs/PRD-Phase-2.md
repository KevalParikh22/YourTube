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
   - Results are merged by reciprocal-rank fusion **with the keyword list at half weight** (spike), with filters for category, playlist and "unwatched only". The top pieces are trimmed to the answer model's context.
3. **Answer:** a new AI task, **Questions and answers**, uses the chosen provider (Phase 1 per-task routing; local by default). The prompt gets the numbered pieces and must cite them as `[n]`; citations are checked in code against the pieces actually sent: invalid numbers are dropped, and a citation whose excerpt matches its sentence much worse than another sent excerpt is **moved to the better match** (embedding similarity; the spike found the small model mis-attributes now and then). Answers are written in the user's preferred language.
4. **Follow-ups:** a short conversation. Follow-up questions are rewritten into standalone questions before retrieval, so "and where does he show the code?" works.
5. **Keeping the index fresh:** an incremental background job (kind `embed`) embeds new or changed pieces after syncs, details fetches, transcriptions and slide reads. Embeddings are tied to the model that made them, and switching models re-embeds.

### Spike results (2026-10-22; scripts `spikes/embed_eval.py`, `spikes/answer_eval.py`)

Library: 360 search pieces from the 3 real videos. 18 hand-written questions, worded to share few words with their answers; 2 are in Hindi. A question counts as found when a top-5 piece is in the right video and time window.

| What | Result | Consequence |
|---|---|---|
| Embedding speed (`qwen3-embedding:0.6b`, Ollama, RTX 3050) | 360 pieces in 31 s (**12 pieces/s**); 1024 dimensions; **1.5 MB** of float32; a query embeds in **12 ms** | Indexing a 1,000-video library (~100k pieces) takes about 2.5 h once, in the background, then is incremental. Brute-force cosine search is instant at this size. |
| Keyword only (FTS5, any term) | 14/18 found, MRR 0.63; **missed both Hindi questions** | Not enough on its own. |
| Meaning only (embeddings) | **18/18**, MRR **0.875**, including both Hindi questions | Meaning-based retrieval is the core. *Correction (step 3): the "offline coding assistant" question counted as found only because its ±30 s window caught neighbouring pieces. The real answer (LM Studio, 12:56) ranks ~55th for "offline" but in the top 7 for "with a local model": the 0.6B model doesn't link "offline" to "running locally".* |
| Hybrid, equal weights (RRF) | 17/18, MRR 0.817; keywords add noise on paraphrases | Don't weight keywords equally. |
| **Hybrid, keyword weight 0.5** | 17/18, MRR **0.872** (≈ meaning only); keeps exact-term matches ("Neo4j", error text) that paraphrased tests can't show | **Chosen.** Send about 8 pieces to the answer model, so rank-6 answers still reach it. |
| Local answers (`qwen3.5:4b`, 8 excerpts) | About **6 s per answer** once warm. **0 invalid citation numbers** out of 20. 4 of 6 answers fully correct; the Hindi question got a correct English answer with 1 unrelated citation. | Usable locally. |
| **Mis-attributed citations** | The small model sometimes puts a correct sentence next to the wrong excerpt (claimed "LM Studio" from an excerpt about the closing verdict) | **Citation check:** compare each answer sentence with all sent excerpts (embeddings) and move a citation to the best-matching excerpt when the cited one matches much worse. Always show each citation's snippet so the user can verify. |

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
- `ask_history`: question, answer, citations JSON, sources JSON (the excerpts sent, cut to 600 characters), filters, mode, model, seconds, created_at.

## 6. Build order (proposed)

1. **Spike:** embed the current library with `qwen3-embedding:0.6b` and measure speed, size and retrieval quality on 15–20 hand-written questions (keyword vs meaning vs hybrid). Check that answer citations from the local 4B model are accurate. **Done 2026-10-22**: see "Spike results" in §3. Hybrid with keyword weight 0.5, plus a citation check.
2. Embedding index plus incremental `embed` job, and hybrid retrieval (`retrieve()`) with tests. **Done 2026-10-22.**
   - `embedding_cache` is keyed by model + text hash (`search_docs.hash`), so re-saved but unchanged text is never embedded again.
   - `embedder.run_embed` (job kind `embed`) runs automatically after sync, details, transcription, slide and rating jobs. It also has `main.py --embed` and `POST /api/embed`.
   - `retrieval.retrieve()`: meaning + keywords (half weight), filters (videos, categories, playlists, include done), at most 4 pieces per video, and keyword-only fallback.
   - Real library: 360 pieces embedded in 9 s. A paraphrased English question finds the LoRA explanation; a Hindi question finds the Neo4j Docker setup in an English video.
   - Fixed along the way: database connections were never closed (a Phase 0 bug); `get_connection()` now closes them on leaving a `with` block.
3. Ask page (A1): answers with checked citations and sources. **Done 2026-10-22.**
   - `asker.ask()`: retrieve 8 pieces → numbered excerpts (cut to the model's context) → the *Questions and answers* task (`tasks.ask`, follows `tasks.rate`) → `check_citations` → saved in `ask_history`.
   - Citation check, in place: `[1, 3]` → `[1][3]`; numbers that weren't sent are dropped; marks on a line of their own join the sentence above; a citation moves to another excerpt only when that one matches the sentence ≥ 0.12 better (cosine) **and** ≥ 0.5 on its own, so "not covered" sentences with equally weak matches aren't shuffled. Duplicates collapse.
   - Calibration on the real library: the local model's citations were the best or tied-best match in every cited sentence of 4 answers (0 moves), at 5–10 s per answer (23 s cold).
   - Retrieval change: the 4-per-video cap now applies after the top 6 pieces, so one clearly relevant video isn't cut short.
   - Ask page: question box, filters (category, playlist, include watched/skipped), the answer with numbered citations that play that moment, cited-video chips, the sources sent (uncited ones dimmed), history with a filter, Copy, Export note (Markdown with timestamp links) and Delete.
   - API: `POST /api/ask {question, filters}`, `GET /api/ask/history?q=`, `GET /api/ask/item?id=`, `GET /api/export/ask?id=`, `POST /api/ask/delete {id}`.
4. Follow-ups, Ask about this video (A2) and semantic search (A3).
5. Setup and status (A4), docs and setup script.

Each step ships with tests and keeps the dashboard working.

## 7. Decisions (2026-10-22)

1. **Retrieval: hybrid.** Keyword (FTS5) plus a local embedding model (`qwen3-embedding:0.6b`, ~0.64 GB) for matching by meaning and across languages.
2. **History: saved.** Questions, answers, citations and filters are kept in `ask_history`; past answers can be re-opened, searched and exported to notes.
3. **Answer model: same as ratings by default.** The new *Questions and answers* task follows the ratings provider unless the user picks another on the AI page.
