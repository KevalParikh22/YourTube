# Follow channels, summarise new videos automatically

**Problem:** You only get summaries for videos you add by hand. Following a channel should bring its new videos in and summarise them for you.

**Status (2026-11-20):** steps 1 to 4 are done; step 5 is open.
- `channels` table (`storage.follow_channel`, `get_channels`, `set_channel`, `unfollow_channel`). Following (`extractor.resolve_channel`: channel link, @handle, channel ID, or a video) records the channel's 15 newest uploads as seen, so only later uploads are added.
- `dashboard._check_channels` reads each channel's uploads playlist (`UU…`, newest 15, `extractor.read_latest_uploads`), adds new videos to the source `_channel-<id>` (no URL, so a re-sync can't pull the whole history), sorts them, fetches details, and queues Rate/Summarise per channel (`auto`: add, details, rate, summarize), through the night queue when that is on.
- Each check queues the next (`not_before`, every 1/3/6/12/24 h, default 6); the first runs 2 min after the dashboard starts. Checks only run while the dashboard runs.
- UI: **Follow channel** in the video panel; Playlists → **Followed channels** (follow by link, what happens to new videos, interval, Check now, Unfollow); Home shows a **New from followed channels** card (up to 5 unwatched uploads from the last 7 days).
- Not done: channel suggestions (step 5). A followed channel is shown as followed in the video panel by its name, not its ID.

**Plan**
1. Data: `channels` table (id, title, url, followed_at, last_checked, auto: none / details / summarise / rate+summarise, library).
2. Check: a scheduled task (every N hours while the dashboard runs) reads each channel's uploads with yt-dlp (`extractor.py` already handles playlist URLs; a channel's `/videos` tab works the same way) and adds new videos to a per-channel source.
3. Process: queue details → rate → summarise for new videos per the channel's setting, respecting the pacer and [off-peak-queue.md](off-peak-queue.md).
4. UI: "Follow channel" button in the video panel and an "Add channel" field in Sources; a "New from followed channels" list on Home.
5. Optional later: suggest channels to follow from the ones you watch most.

**Done when:** following a channel brings in its next upload within one check interval, with a summary ready, without manual steps.
