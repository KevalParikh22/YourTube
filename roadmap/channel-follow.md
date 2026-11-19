# Follow channels, summarise new videos automatically

**Problem:** You only get summaries for videos you add by hand. Following a channel should bring its new videos in and summarise them for you.

**Plan**
1. Data: `channels` table (id, title, url, followed_at, last_checked, auto: none / details / summarise / rate+summarise, library).
2. Check: a scheduled task (every N hours while the dashboard runs) reads each channel's uploads with yt-dlp (`extractor.py` already handles playlist URLs; a channel's `/videos` tab works the same way) and adds new videos to a per-channel source.
3. Process: queue details → rate → summarise for new videos per the channel's setting, respecting the pacer and [off-peak-queue.md](off-peak-queue.md).
4. UI: "Follow channel" button in the video panel and an "Add channel" field in Sources; a "New from followed channels" list on Home.
5. Optional later: suggest channels to follow from the ones you watch most.

**Done when:** following a channel brings in its next upload within one check interval, with a summary ready, without manual steps.
