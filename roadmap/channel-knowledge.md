# Whole channel into the knowledge base

**Problem:** Knowledge pages are built per category (`knowledge.py`). A channel that teaches one subject should become one knowledge base you can read and ask.

**Plan**
1. Import a full channel as a source (same as [channel-follow.md](channel-follow.md) step 2, but all past uploads, with a count and cost estimate first).
2. Fetch details/transcripts for all, then build knowledge pages scoped to that channel (add `channel` as a scope next to category in `knowledge.py`).
3. Ask scoped to a channel (retrieval already filters by category/source; add channel).
4. Big channels (500+ videos) go through the night queue.

**Done when:** "Import channel X into knowledge" produces topic pages with citations to its videos and Ask can be limited to that channel.
