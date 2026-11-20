# Whole channel into the knowledge base

**Problem:** Knowledge pages are built per category (`knowledge.py`). A channel that teaches one subject should become one knowledge base you can read and ask.

**Status: done.**
- Playlists → Followed channels → **Import all**: reads every upload of the channel (anonymously), shows the video count,
  hours, how many are new and the estimated summary cost (`summarizer.estimate_by_length`, from video lengths since the
  transcripts aren't fetched yet), then adds them all to the channel's source, fetches details and transcripts, and queues
  summaries for the ones without one (50 per task). 500+ to summarise: every summary task waits for the night queue's start.
- Videos already in the library keep their category; videos removed from YourTube stay out.
- Knowledge: `knowledge.py` takes a `channel` scope next to the library (pages saved as `_channel-<id>/<category>`);
  Knowledge page → **Pages from** picks a followed channel (`#/knowledge?channel=UC…`), or Channels card → **Knowledge**;
  **Build all** builds every out-of-date page and every unbuilt topic with 3+ summaries (`POST /api/knowledge/build-all`).
- Ask: retrieval already filters by source, so **Ask** on a channel opens Ask limited to the channel's source.
- Topic pages build by themselves after an import (`dashboard._build_channel_pages`): it waits while any Summarise task runs or is queued, in any lane, retrying every 30 min, then builds topics with 3+ summaries. The import estimate leaves out videos removed from YourTube.

**Plan**
1. Import a full channel as a source (same as [channel-follow.md](channel-follow.md) step 2, but all past uploads, with a count and cost estimate first).
2. Fetch details/transcripts for all, then build knowledge pages scoped to that channel (add `channel` as a scope next to category in `knowledge.py`).
3. Ask scoped to a channel (retrieval already filters by category/source; add channel).
4. Big channels (500+ videos) go through the night queue.

**Done when:** "Import channel X into knowledge" produces topic pages with citations to its videos and Ask can be limited to that channel.
