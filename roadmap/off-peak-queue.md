# Schedule big summaries for the night

**Problem:** A 3–5 hour video eats a large share of a provider's per-minute or daily limit and blocks the cloud lane for everything else.

**Plan**
1. Setting: "Videos longer than [90] min: run AI tasks [now / at night]" plus a night window (default 01:00–06:00) and an optional daily token cap (from [token-tracking.md](token-tracking.md)).
2. `Job.start` gets an optional `not_before` time; long-video tasks are queued with it and shown in the task list as "Waiting for 01:00 (long video)". A "Run now" button clears it.
3. The lane worker skips tasks whose `not_before` is in the future; a timer thread wakes the lane at the window start. The dashboard must be running (service mode, `scripts/service.sh`).
4. Split very long transcripts into chunks and summarise in parts (also needed by [video-course.md](video-course.md)).

**Done when:** a 4 h video selected for Summarise in the afternoon waits, runs at night, and the morning view shows it done without blocking daytime tasks.
