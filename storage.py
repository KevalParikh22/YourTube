"""
Database storage layer using SQLite for local, persistent storage.
Tracks playlists, videos, playlist membership, categories, and watch completion statuses.

Membership lives in `playlist_items` so one video can belong to several playlists, and
videos that disappear from a source playlist are flagged (`removed = 1`) rather than deleted.
Manual category assignments are protected by `videos.category_locked` and survive re-ingestion.
`videos.category_source` records where a category came from: 'keywords' (automatic, unlocked),
'manual' (set by hand) or 'ai' (an accepted AI suggestion). AI suggestions wait for review in
`category_suggestions` and never change a video until the user accepts them.
"""
import sqlite3
import os
import json
import functools
import threading
from typing import List, Dict, Any, Optional, Callable, Iterable

DB_PATH = os.environ.get("YTKB_DB") or os.path.join(os.path.dirname(__file__), "yt_kb.db")
VALID_STATUSES = ("unwatched", "in_progress", "completed", "skipped")
# Statuses that no longer count as remaining work ("skipped" = decided not to watch)
DONE_STATUSES = ("completed", "skipped")
FALLBACK_CATEGORY = "General / Other"

class _ClosingConnection(sqlite3.Connection):
    """sqlite3's own `with conn:` only commits or rolls back; this also closes, so connections don't leak."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn

def _columns(cursor: sqlite3.Cursor, table: str) -> set:
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}

_STATUS_CHECK = ", ".join(f"'{s}'" for s in VALID_STATUSES)
VIDEOS_DDL = f"""
    CREATE TABLE IF NOT EXISTS {{name}} (
        video_id TEXT PRIMARY KEY,
        playlist_id TEXT,
        title TEXT NOT NULL,
        channel TEXT,
        duration_seconds INTEGER DEFAULT 0,
        url TEXT NOT NULL,
        category TEXT DEFAULT 'Uncategorized',
        status TEXT CHECK(status IN ({_STATUS_CHECK})) DEFAULT 'unwatched',
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        category_locked INTEGER NOT NULL DEFAULT 0,
        notes TEXT DEFAULT '',
        status_updated_at TIMESTAMP,
        category_source TEXT NOT NULL DEFAULT 'keywords',
        has_visuals INTEGER,
        FOREIGN KEY(playlist_id) REFERENCES playlists(id)
    )
"""

def _rebuild_videos_if_status_check_outdated(cursor: sqlite3.Cursor) -> None:
    """SQLite cannot alter a CHECK constraint, so copy into a table with the current one."""
    sql = cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'videos'").fetchone()[0]
    if all(f"'{s}'" in sql for s in VALID_STATUSES):
        return
    cursor.execute(VIDEOS_DDL.format(name="videos_new"))
    cols = ", ".join(sorted(_columns(cursor, "videos") & _columns(cursor, "videos_new")))
    cursor.execute(f"INSERT INTO videos_new ({cols}) SELECT {cols} FROM videos")
    cursor.execute("DROP TABLE videos")
    cursor.execute("ALTER TABLE videos_new RENAME TO videos")

def init_db(db_path: str = DB_PATH) -> None:
    """Initializes tables if they do not exist and migrates older databases in place."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                original_url TEXT,
                total_videos INTEGER DEFAULT 0,
                total_duration_seconds INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        playlist_cols = _columns(cursor, "playlists")
        for col in ("access", "cookies_file"):
            if col not in playlist_cols:
                cursor.execute(f"ALTER TABLE playlists ADD COLUMN {col} TEXT")

        cursor.execute(VIDEOS_DDL.format(name="videos"))

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_items (
                playlist_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                position INTEGER DEFAULT 0,
                removed INTEGER NOT NULL DEFAULT 0,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(playlist_id, video_id),
                FOREIGN KEY(playlist_id) REFERENCES playlists(id),
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS category_suggestions (
                video_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                confidence REAL,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_details (
                video_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                chapters TEXT DEFAULT '[]',
                language TEXT,
                yt_categories TEXT DEFAULT '[]',
                caption_lang TEXT,
                caption_kind TEXT,
                tries INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transcript_segments (
                video_id TEXT NOT NULL,
                start_s REAL NOT NULL,
                dur_s REAL,
                text TEXT NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_segments_video ON transcript_segments(video_id, start_s)")
        for col, ddl in (("upload_date", "TEXT"),
                         ("transcript_source", "TEXT"),      # chosen transcript; NULL = captions, else the local one
                         ("asr_language", "TEXT"), ("asr_model", "TEXT"), ("asr_at", "TIMESTAMP")):
            if col not in _columns(cursor, "video_details"):
                cursor.execute(f"ALTER TABLE video_details ADD COLUMN {col} {ddl}")
        if "source" not in _columns(cursor, "transcript_segments"):
            # 'captions' (YouTube) or 'local-asr' (Whisper on this computer); a video can have both
            cursor.execute("ALTER TABLE transcript_segments ADD COLUMN source TEXT NOT NULL DEFAULT 'captions'")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS visual_segments (
                video_id TEXT NOT NULL,
                t_s REAL NOT NULL,
                text TEXT NOT NULL DEFAULT '',      -- on-screen text in reading order
                visual TEXT NOT NULL DEFAULT '',    -- one-sentence description of a diagram or whiteboard
                thumb TEXT,                         -- file name under frames/<video_id>/
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_visual_video ON visual_segments(video_id, t_s)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS media_jobs (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL,
                kind TEXT NOT NULL,                  -- 'asr' (L1) or 'visual' (L2)
                status TEXT NOT NULL,                -- queued, downloading, transcribing / reading, done, failed
                tries INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                progress REAL,
                timings TEXT,                        -- JSON: download_s, transcribe_s, audio_s, ...
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                UNIQUE(video_id, kind)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_ratings (
                video_id TEXT PRIMARY KEY,
                score REAL NOT NULL,
                verdict TEXT NOT NULL CHECK(verdict IN ('watch', 'skim', 'skip')),
                reason TEXT DEFAULT '',
                tldr TEXT DEFAULT '',
                points TEXT DEFAULT '[]',
                skim TEXT DEFAULT '[]',
                basis TEXT,
                language TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)

        # Migrate databases created before these columns existed
        existing = _columns(cursor, "videos")
        for col, ddl in (
            ("category_locked", "INTEGER NOT NULL DEFAULT 0"),
            ("notes", "TEXT DEFAULT ''"),
            ("status_updated_at", "TIMESTAMP"),
            ("category_source", "TEXT NOT NULL DEFAULT 'keywords'"),
            ("has_visuals", "INTEGER"),          # slides/whiteboard: NULL = not set, 0/1 = set by the user
        ):
            if col not in existing:
                cursor.execute(f"ALTER TABLE videos ADD COLUMN {col} {ddl}")
                if col == "category_source":
                    cursor.execute("UPDATE videos SET category_source = 'manual' WHERE category_locked = 1")
        _rebuild_videos_if_status_check_outdated(cursor)

        # Backfill membership from the legacy single playlist_id column
        cursor.execute("""
            INSERT OR IGNORE INTO playlist_items (playlist_id, video_id, position)
            SELECT playlist_id, video_id, rowid FROM videos WHERE playlist_id IS NOT NULL
        """)

        # Separate libraries: every playlist belongs to one; library 1 always exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS libraries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("INSERT OR IGNORE INTO libraries (id, name) VALUES (1, ?)", (DEFAULT_LIBRARY_NAME,))
        if "library_id" not in _columns(cursor, "playlists"):
            cursor.execute("ALTER TABLE playlists ADD COLUMN library_id INTEGER NOT NULL DEFAULT 1")
        # Summaries (Phase 3): made when the user clicks Summarise, independent of the personal rating
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS video_summaries (
                video_id TEXT PRIMARY KEY,
                tldr TEXT DEFAULT '',
                minutes TEXT DEFAULT '[]',
                claims TEXT DEFAULT '[]',
                quiz TEXT DEFAULT '[]',
                takeaways TEXT DEFAULT '[]',
                learn_more TEXT DEFAULT '{}',
                model TEXT,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(video_id) REFERENCES videos(video_id)
            )
        """)
        # Knowledge pages (Phase 3): one per category of a library, built from its videos' summaries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topic_pages (
                library_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '{}',
                sources TEXT NOT NULL DEFAULT '[]',
                video_ids_hash TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (library_id, category)
            )
        """)
        # Explainable ratings: per-factor scores, meeting-style minutes and takeaways
        rating_cols = _columns(cursor, "video_ratings")
        for col, ddl in (("breakdown", "TEXT DEFAULT '{}'"), ("minutes", "TEXT DEFAULT '[]'"), ("takeaways", "TEXT DEFAULT '[]'")):
            if col not in rating_cols:
                cursor.execute(f"ALTER TABLE video_ratings ADD COLUMN {col} {ddl}")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_playlist ON videos(playlist_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_video ON playlist_items(video_id)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ask_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                citations TEXT NOT NULL DEFAULT '[]',
                sources TEXT NOT NULL DEFAULT '[]',
                filters TEXT NOT NULL DEFAULT '{}',
                mode TEXT,
                model TEXT,
                seconds REAL,
                thread_id INTEGER,
                standalone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        asks = _columns(cursor, "ask_history")
        for col, ddl in (("thread_id", "INTEGER"), ("standalone", "TEXT")):   # follow-ups (Phase 2 step 4)
            if col not in asks:
                cursor.execute(f"ALTER TABLE ask_history ADD COLUMN {col} {ddl}")
        cursor.execute("UPDATE ask_history SET thread_id = id WHERE thread_id IS NULL")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ask_thread ON ask_history(thread_id)")

        if _create_search_tables(cursor):
            _reindex(cursor, [r[0] for r in cursor.execute("SELECT video_id FROM videos")])
        conn.commit()

SINGLE_SOURCE = {"id": "_single", "title": "Added one by one", "url": ""}   # source for videos added by their own link
DEFAULT_LIBRARY_NAME = "My library"

def save_playlist(playlist_info: Dict[str, Any], videos: List[Dict[str, Any]], db_path: str = DB_PATH,
                  partial: bool = False, library_id: Optional[int] = None) -> Dict[str, int]:
    """
    Saves or updates a playlist and its videos.
    Keeps existing watch status, notes and manually locked categories.
    Videos no longer present in the source are flagged removed, never deleted. With partial=True (videos added one
    by one) the list only adds: nothing is flagged removed and the totals count every video in the source.
    Returns counts: {'added', 'updated', 'removed'}.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        playlist_id = playlist_info['id']

        known = {row[0] for row in cursor.execute(
            "SELECT video_id FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
        ).fetchall()}

        cursor.execute("""
            INSERT INTO playlists (id, title, original_url, total_videos, total_duration_seconds, updated_at, library_id, access, cookies_file)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                original_url = COALESCE(NULLIF(excluded.original_url, ''), playlists.original_url),
                total_videos = excluded.total_videos,
                total_duration_seconds = excluded.total_duration_seconds,
                updated_at = CURRENT_TIMESTAMP,
                access = COALESCE(excluded.access, playlists.access),
                cookies_file = COALESCE(excluded.cookies_file, playlists.cookies_file)
        """, (
            playlist_id,
            playlist_info.get('title', 'Untitled Playlist'),
            playlist_info.get('url', ''),
            len(videos),
            sum(v.get('duration_seconds', 0) for v in videos),
            int(library_id or 1),   # only for a new playlist: a re-sync keeps the library it was moved to
            playlist_info.get('access'), playlist_info.get('cookies_file'),
        ))

        for idx, v in enumerate(videos):
            cursor.execute("""
                INSERT INTO videos (video_id, playlist_id, title, channel, duration_seconds, url, category, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'unwatched')
                ON CONFLICT(video_id) DO UPDATE SET
                    playlist_id = excluded.playlist_id,
                    title = excluded.title,
                    channel = excluded.channel,
                    duration_seconds = excluded.duration_seconds,
                    url = excluded.url,
                    category = CASE WHEN videos.category_locked = 1 THEN videos.category ELSE excluded.category END
            """, (
                v['video_id'],
                playlist_id,
                v.get('title', 'Unknown Title'),
                v.get('channel', 'Unknown Channel'),
                v.get('duration_seconds', 0),
                v.get('url', f"https://www.youtube.com/watch?v={v['video_id']}"),
                v.get('category', 'Uncategorized'),
            ))
            cursor.execute("""
                INSERT INTO playlist_items (playlist_id, video_id, position, removed)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(playlist_id, video_id) DO UPDATE SET
                    position = excluded.position,
                    removed = 0
            """, (playlist_id, v['video_id'], v.get('position', idx)))

        current_ids = [v['video_id'] for v in videos]
        removed = 0
        if partial:
            cursor.execute("""
                UPDATE playlists SET
                    total_videos = (SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?),
                    total_duration_seconds = (SELECT COALESCE(SUM(v.duration_seconds), 0) FROM playlist_items i
                                              JOIN videos v ON v.video_id = i.video_id WHERE i.playlist_id = ?)
                WHERE id = ?""", (playlist_id, playlist_id, playlist_id))
        # An empty extraction is far more likely a fetch failure than an emptied playlist
        elif current_ids:
            cursor.execute("""
                UPDATE playlist_items SET removed = 1
                WHERE playlist_id = ? AND removed = 0
                  AND video_id NOT IN (SELECT value FROM json_each(?))
            """, (playlist_id, json.dumps(current_ids)))
            removed = cursor.rowcount
        _reindex(cursor, current_ids, kinds=("title",))
        conn.commit()

    added = len(set(current_ids) - known)
    return {'added': added, 'updated': len(current_ids) - added, 'removed': removed}

def get_all_videos(
    playlist_id: Optional[str] = None,
    include_removed: bool = False,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Retrieves videos. With a playlist ID, returns that playlist's items in source order.
    Without one, returns every video in the library (deduplicated across playlists).
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if playlist_id:
            cursor.execute(f"""
                SELECT v.*, pi.position AS position, pi.removed AS removed
                FROM playlist_items pi JOIN videos v ON v.video_id = pi.video_id
                WHERE pi.playlist_id = ? {'' if include_removed else 'AND pi.removed = 0'}
                ORDER BY pi.position
            """, (playlist_id,))
            rows = [dict(row) for row in cursor.fetchall()]
            for r in rows:
                r['playlist_id'] = playlist_id
                r['playlist_ids'] = [playlist_id]
            return rows

        cursor.execute("""
            SELECT v.*,
                   COALESCE(MIN(pi.position), 0) AS position,
                   COALESCE(MIN(pi.removed), 0) AS removed,
                   GROUP_CONCAT(CASE WHEN pi.removed = 0 THEN pi.playlist_id END) AS playlist_ids
            FROM videos v LEFT JOIN playlist_items pi ON pi.video_id = v.video_id
            GROUP BY v.video_id
            ORDER BY v.category, v.title
        """)
        rows = []
        for row in cursor.fetchall():
            r = dict(row)
            r['playlist_ids'] = r['playlist_ids'].split(',') if r['playlist_ids'] else []
            if include_removed or not r['removed']:
                rows.append(r)
        return rows

def get_library(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Every video in the library, including ones no longer present in any source playlist
    (the library keeps them until the user marks them watched). Each video carries
    `sources`: [{'id', 'position', 'removed'}] for every playlist it has appeared in.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        videos = {row['video_id']: {**dict(row), 'sources': []}
                  for row in cursor.execute("SELECT * FROM videos ORDER BY added_at, rowid")}
        for row in cursor.execute("SELECT i.playlist_id, i.video_id, i.position, i.removed, p.library_id FROM playlist_items i "
                                  "LEFT JOIN playlists p ON p.id = i.playlist_id ORDER BY i.rowid"):
            v = videos.get(row['video_id'])
            if v is not None:
                v['sources'].append({'id': row['playlist_id'], 'position': row['position'], 'removed': row['removed'],
                                     'library': row['library_id'] or 1})
        suggestions = {row['video_id']: {'category': row['category'], 'confidence': row['confidence']}
                       for row in cursor.execute("SELECT video_id, category, confidence FROM category_suggestions")}
        details = {row['video_id']: {'status': row['status'], 'caption_kind': row['caption_kind'],
                                     'caption_lang': row['caption_lang'], 'chapters': row['n_chapters'],
                                     'tries': row['tries'], 'asr_language': row['asr_language']}
                   for row in cursor.execute("SELECT video_id, status, caption_kind, caption_lang, tries, asr_language, "
                                             "json_array_length(chapters) AS n_chapters FROM video_details")}
        for vid, d in details.items():
            d['sources'] = _sources(conn, vid)
            d['transcript'] = _active_source(conn, vid)
        jobs = {(row['video_id'], row['kind']): {'status': row['status'], 'error': row['error'], 'tries': row['tries'],
                                                 'progress': row['progress']}
                for row in cursor.execute("SELECT video_id, kind, status, error, tries, progress FROM media_jobs")}
        slides = dict(cursor.execute("SELECT video_id, COUNT(*) FROM visual_segments GROUP BY video_id").fetchall())
        ratings = {}
        for row in cursor.execute("SELECT * FROM video_ratings"):
            r = dict(row)
            r['points'], r['skim'] = json.loads(r['points'] or '[]'), json.loads(r['skim'] or '[]')
            r['breakdown'] = json.loads(r.get('breakdown') or '{}')
            r['calc'] = r['breakdown'].pop('calc', None)   # how the score was computed (rater.compute_score)
            r['minutes'], r['takeaways'] = json.loads(r.get('minutes') or '[]'), json.loads(r.get('takeaways') or '[]')
            ratings[r.pop('video_id')] = r
    reasons = {c['video_id']: c['reason'] for c in videos_needing_asr(db_path=db_path)}
    hints = visual_hints(db_path=db_path)
    for v in videos.values():
        if v['video_id'] in details:
            details[v['video_id']]['asr'] = jobs.get((v['video_id'], 'asr'))
            details[v['video_id']]['asr_reason'] = reasons.get(v['video_id'])
        v['visual'] = {'slides': slides.get(v['video_id'], 0), 'job': jobs.get((v['video_id'], 'visual')),
                       'hint': hints.get(v['video_id'])}
        v['rating'] = ratings.get(v['video_id'])
        v['suggestion'] = suggestions.get(v['video_id'])
        v['details'] = details.get(v['video_id'])
        v['in_source'] = any(not s['removed'] for s in v['sources'])
        v['library'] = _video_library(v['sources'])
        v['libraries'] = sorted({s['library'] for s in v['sources']}) or [1]
    return list(videos.values())

def _video_library(sources: List[Dict[str, Any]]) -> int:
    """A video's library: that of its first playlist still containing it, else its first playlist, else 1."""
    live = [s for s in sources if not s['removed']]
    return (live or sources or [{'library': 1}])[0]['library'] or 1

def get_playlists(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lists ingested playlists with live (non-removed) counts and durations."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.title, p.original_url, p.created_at, p.updated_at, p.library_id, p.access, p.cookies_file,
                   SUM(CASE WHEN pi.removed = 0 THEN 1 ELSE 0 END) AS active_videos,
                   SUM(CASE WHEN pi.removed = 1 THEN 1 ELSE 0 END) AS removed_videos,
                   SUM(CASE WHEN pi.removed = 0 THEN v.duration_seconds ELSE 0 END) AS duration_seconds
            FROM playlists p
            LEFT JOIN playlist_items pi ON pi.playlist_id = p.id
            LEFT JOIN videos v ON v.video_id = pi.video_id
            GROUP BY p.id
            ORDER BY p.updated_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_playlist(playlist_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return dict(row) if row else None

def update_videos(
    video_ids: Iterable[str],
    status: Optional[str] = None,
    category: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: str = DB_PATH
) -> int:
    """
    Applies status / category / notes to one or more videos.
    Setting a category marks it as a manual (locked) assignment.
    """
    ids = list(video_ids)
    if not ids:
        return 0
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    sets, params = [], []
    if status is not None:
        sets.append("status = ?, status_updated_at = CURRENT_TIMESTAMP")
        params.append(status)
    if category is not None:
        sets.append("category = ?, category_locked = 1, category_source = 'manual'")
        params.append(category)
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return 0

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE videos SET {', '.join(sets)} WHERE video_id IN (SELECT value FROM json_each(?))",
            (*params, json.dumps(ids))
        )
        updated = cursor.rowcount
        if notes is not None:
            _reindex(cursor, ids, kinds=("notes",))
        if category is not None:
            # A hand-picked category answers any pending AI suggestion
            cursor.execute("DELETE FROM category_suggestions WHERE video_id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))
        conn.commit()
        return updated

def reclassify_videos(
    classify: Callable[[str, str], str],
    video_ids: Optional[Iterable[str]] = None,
    unlock: bool = False,
    db_path: str = DB_PATH
) -> int:
    """
    Re-runs auto-classification. Without video_ids, touches every unlocked video.
    With video_ids and unlock=True, resets those videos to automatic classification.
    Returns the number of videos whose category changed.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if video_ids is not None:
            ids = json.dumps(list(video_ids))
            if unlock:
                cursor.execute("UPDATE videos SET category_locked = 0, category_source = 'keywords' "
                               "WHERE video_id IN (SELECT value FROM json_each(?))", (ids,))
            rows = cursor.execute(
                "SELECT video_id, title, channel, category FROM videos "
                "WHERE category_locked = 0 AND video_id IN (SELECT value FROM json_each(?))", (ids,)
            ).fetchall()
        else:
            rows = cursor.execute(
                "SELECT video_id, title, channel, category FROM videos WHERE category_locked = 0"
            ).fetchall()

        changed = 0
        for r in rows:
            new_cat = classify(r['title'] or "", r['channel'] or "")
            if new_cat != r['category']:
                cursor.execute("UPDATE videos SET category = ? WHERE video_id = ?", (new_cat, r['video_id']))
                changed += 1
        conn.commit()
        return changed

def _only(video_ids: Optional[Iterable[str]]) -> tuple:
    """SQL condition and parameter limiting a statement to some videos (None = all)."""
    if video_ids is None:
        return "", ()
    return " AND video_id IN (SELECT value FROM json_each(?))", (json.dumps(list(video_ids)),)

def rename_category(old: str, new: str, db_path: str = DB_PATH, video_ids: Optional[Iterable[str]] = None) -> int:
    """Renames a category on every video (or the given ones, e.g. one library's) that carries it; locks are kept."""
    cond, extra = _only(video_ids)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE videos SET category = ? WHERE category = ?" + cond, (new, old, *extra))
        n = cursor.rowcount
        cursor.execute("UPDATE category_suggestions SET category = ? WHERE category = ?" + cond, (new, old, *extra))
        conn.commit()
        return n

def release_category(name: str, db_path: str = DB_PATH, video_ids: Optional[Iterable[str]] = None) -> int:
    """Moves a deleted category's videos (all, or the given ones) to the fallback category and unlocks them."""
    cond, extra = _only(video_ids)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE videos SET category = ?, category_locked = 0, category_source = 'keywords' WHERE category = ?" + cond,
            (FALLBACK_CATEGORY, name, *extra)
        )
        n = cursor.rowcount
        cursor.execute("DELETE FROM category_suggestions WHERE category = ?" + cond, (name, *extra))
        conn.commit()
        return n

# --- AI category suggestions -------------------------------------------------

def videos_for_ai(scope: str = "unsorted", db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Videos the AI may suggest a category for. Hand-set and accepted categories are never offered.
    'unsorted': unlocked videos in the fallback bucket that have no pending suggestion.
    'unlocked': every unlocked video (re-checks keyword-sorted ones too).
    """
    if scope not in ("unsorted", "unlocked"):
        raise ValueError(f"Invalid scope: {scope}")
    init_db(db_path)
    sql = "SELECT video_id, title, channel, category FROM videos WHERE category_locked = 0"
    if scope == "unsorted":
        sql += (" AND category IN (?, 'Uncategorized')"
                " AND video_id NOT IN (SELECT video_id FROM category_suggestions)")
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (FALLBACK_CATEGORY,) if scope == "unsorted" else ()).fetchall()
        return [dict(r) for r in rows]

def save_suggestions(items: Iterable[Dict[str, Any]], model: str = "", db_path: str = DB_PATH) -> int:
    """
    Stores AI suggestions [{'video_id', 'category', 'confidence'}] for review.
    Skips locked videos and suggestions that match the current category. Returns the number stored.
    """
    init_db(db_path)
    stored = 0
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        for it in items:
            row = cursor.execute("SELECT category, category_locked FROM videos WHERE video_id = ?",
                                 (it['video_id'],)).fetchone()
            if row is None or row['category_locked']:
                continue
            if it['category'] == row['category']:
                cursor.execute("DELETE FROM category_suggestions WHERE video_id = ?", (it['video_id'],))
                continue
            cursor.execute("""
                INSERT INTO category_suggestions (video_id, category, confidence, model, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    category = excluded.category, confidence = excluded.confidence,
                    model = excluded.model, created_at = CURRENT_TIMESTAMP
            """, (it['video_id'], it['category'], it.get('confidence'), model))
            stored += 1
        conn.commit()
    return stored

def _suggestion_filter(video_ids: Optional[Iterable[str]], min_confidence: Optional[float]):
    where, params = [], []
    if video_ids is not None:
        where.append("video_id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(list(video_ids)))
    if min_confidence is not None:
        where.append("COALESCE(confidence, 0) >= ?")
        params.append(float(min_confidence))
    return (" WHERE " + " AND ".join(where)) if where else "", params

def accept_suggestions(
    video_ids: Optional[Iterable[str]] = None,
    min_confidence: Optional[float] = None,
    db_path: str = DB_PATH
) -> int:
    """Applies pending suggestions (all, by ID, and/or above a confidence) and locks them as 'ai'."""
    init_db(db_path)
    where, params = _suggestion_filter(video_ids, min_confidence)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        rows = cursor.execute(f"SELECT video_id, category FROM category_suggestions{where}", params).fetchall()
        for r in rows:
            cursor.execute("UPDATE videos SET category = ?, category_locked = 1, category_source = 'ai' "
                           "WHERE video_id = ?", (r['category'], r['video_id']))
            cursor.execute("DELETE FROM category_suggestions WHERE video_id = ?", (r['video_id'],))
        conn.commit()
        return len(rows)

def dismiss_suggestions(video_ids: Optional[Iterable[str]] = None, db_path: str = DB_PATH) -> int:
    """Discards pending suggestions without changing any video."""
    init_db(db_path)
    where, params = _suggestion_filter(video_ids, None)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM category_suggestions{where}", params)
        conn.commit()
        return cursor.rowcount

def ai_context(video_ids: Iterable[str], excerpt_chars: int = 1000, db_path: str = DB_PATH) -> Dict[str, Dict[str, str]]:
    """
    Extra text that helps the AI sort videos: {video_id: {'description', 'summary', 'excerpt'}}.
    'summary' is the AI rating's TL;DR; 'excerpt' is the opening of the transcript. Missing pieces are ''.
    """
    ids = list(video_ids)
    init_db(db_path)
    out = {vid: {"description": "", "summary": "", "excerpt": ""} for vid in ids}
    with get_connection(db_path) as conn:
        q = "SELECT value FROM json_each(?)"
        for r in conn.execute(f"SELECT video_id, description FROM video_details WHERE video_id IN ({q})", (json.dumps(ids),)):
            out[r['video_id']]["description"] = r['description'] or ""
        for r in conn.execute(f"SELECT video_id, tldr FROM video_ratings WHERE video_id IN ({q})", (json.dumps(ids),)):
            out[r['video_id']]["summary"] = r['tldr'] or ""
        for vid in ids:
            parts, size = [], 0
            for (text,) in conn.execute("SELECT text FROM transcript_segments WHERE video_id = ? AND source = ? "
                                        "ORDER BY start_s", (vid, _active_source(conn, vid))):
                parts.append(text)
                size += len(text) + 1
                if size > excerpt_chars:
                    break
            out[vid]["excerpt"] = " ".join(parts)[:excerpt_chars]
    return out

# --- Video details and transcripts (Phase 1) --------------------------------------

def videos_needing_details(
    video_ids: Optional[Iterable[str]] = None,
    max_tries: int = 3,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """
    Videos with no details yet, or whose last fetch failed fewer than max_tries times.
    With video_ids, those videos are fetched again whatever their state (an explicit request).
    Not-done videos come first, oldest first.
    """
    init_db(db_path)
    sql = """
        SELECT v.video_id, v.title, v.category, v.status
        FROM videos v LEFT JOIN video_details d ON d.video_id = v.video_id
    """
    with get_connection(db_path) as conn:
        if video_ids is not None:
            rows = conn.execute(sql + " WHERE v.video_id IN (SELECT value FROM json_each(?))",
                                (json.dumps(list(video_ids)),)).fetchall()
        else:
            rows = conn.execute(sql + """
                WHERE d.video_id IS NULL OR (d.status = 'failed' AND d.tries < ?)
                ORDER BY CASE WHEN v.status IN ('completed', 'skipped') THEN 1 ELSE 0 END, v.added_at, v.rowid
            """, (max_tries,)).fetchall()
        return [dict(r) for r in rows]

def save_details(video_id: str, result: Dict[str, Any], db_path: str = DB_PATH) -> None:
    """Stores one fetch result (see enrich.fetch_details). Failures keep earlier good data and count tries."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        prev = cursor.execute("SELECT status, tries FROM video_details WHERE video_id = ?", (video_id,)).fetchone()
        if result['status'] == 'failed' and prev is not None and prev['status'] not in ('failed',):
            # A retry that failed: keep the earlier result
            cursor.execute("UPDATE video_details SET error = ? WHERE video_id = ?", (result.get('error'), video_id))
            conn.commit()
            return
        tries = (prev['tries'] if prev is not None else 0) + 1 if result['status'] == 'failed' else 0
        cursor.execute("""
            INSERT INTO video_details (video_id, status, description, tags, chapters, language, yt_categories,
                                       caption_lang, caption_kind, tries, error, upload_date, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                status = excluded.status, description = excluded.description, tags = excluded.tags,
                chapters = excluded.chapters, language = excluded.language, yt_categories = excluded.yt_categories,
                caption_lang = excluded.caption_lang, caption_kind = excluded.caption_kind,
                tries = excluded.tries, error = excluded.error, upload_date = excluded.upload_date,
                fetched_at = CURRENT_TIMESTAMP
        """, (
            video_id, result['status'], result.get('description') or '',
            json.dumps(result.get('tags') or [], ensure_ascii=False),
            json.dumps(result.get('chapters') or [], ensure_ascii=False),
            result.get('language'), json.dumps(result.get('yt_categories') or [], ensure_ascii=False),
            result.get('caption_lang'), result.get('caption_kind'), tries, result.get('error'),
            result.get('upload_date'),
        ))
        if result['status'] != 'failed':
            # Only YouTube captions are replaced; a local transcript stays
            cursor.execute("DELETE FROM transcript_segments WHERE video_id = ? AND source = 'captions'", (video_id,))
            cursor.executemany(
                "INSERT INTO transcript_segments (video_id, start_s, dur_s, text, source) VALUES (?, ?, ?, ?, 'captions')",
                [(video_id, seg['start'], seg.get('dur'), seg['text']) for seg in result.get('segments') or []])
            _reindex(cursor, [video_id], kinds=("description", "chapter", "transcript"))
        conn.commit()

def get_details(video_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Full details for one video, with its transcript segments, or None if never fetched."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM video_details WHERE video_id = ?", (video_id,)).fetchone()
        if row is None:
            visuals = get_visuals(video_id, db_path=db_path, conn=conn)
            # Slides can be read before details are fetched
            return {"status": None, "description": "", "chapters": [], "tags": [], "segments": [], "sources": [],
                    "transcript": "captions", "visuals": visuals} if visuals else None
        d = dict(row)
        for key in ('tags', 'chapters', 'yt_categories'):
            d[key] = json.loads(d[key] or '[]')
        d['sources'] = _sources(conn, video_id)
        d['transcript'] = _active_source(conn, video_id)
        d['visuals'] = get_visuals(video_id, db_path=db_path, conn=conn)
        d['segments'] = [dict(r) for r in conn.execute(
            "SELECT start_s AS start, dur_s AS dur, text FROM transcript_segments WHERE video_id = ? AND source = ? "
            "ORDER BY start_s", (video_id, d['transcript']))]
        return d

# --- AI watch ratings (Phase 1) -----------------------------------------------------

def videos_for_rating(video_ids: Optional[Iterable[str]] = None, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Videos to rate with what the rater needs (details may be missing).
    Without video_ids: videos not yet done and not yet rated. With video_ids: exactly those (re-rate).
    """
    init_db(db_path)
    sql = """
        SELECT v.video_id, v.title, v.channel, v.duration_seconds, v.category, v.status,
               d.status AS details_status, d.description, d.chapters, d.upload_date
        FROM videos v LEFT JOIN video_details d ON d.video_id = v.video_id
    """
    with get_connection(db_path) as conn:
        if video_ids is not None:
            rows = conn.execute(sql + " WHERE v.video_id IN (SELECT value FROM json_each(?))",
                                (json.dumps(list(video_ids)),)).fetchall()
        else:
            rows = conn.execute(sql + """
                WHERE v.status NOT IN ('completed', 'skipped')
                  AND v.video_id NOT IN (SELECT video_id FROM video_ratings)
                ORDER BY v.added_at, v.rowid
            """).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['chapters'] = json.loads(d['chapters'] or '[]')
            out.append(d)
        return out

def get_segments(video_id: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT start_s AS start, text FROM transcript_segments WHERE video_id = ? AND source = ? ORDER BY start_s",
            (video_id, _active_source(conn, video_id)))]

def save_rating(video_id: str, rating: Dict[str, Any], model: str = "", db_path: str = DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO video_ratings (video_id, score, verdict, reason, tldr, points, skim, basis, language, model,
                                       breakdown, minutes, takeaways, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                score = excluded.score, verdict = excluded.verdict, reason = excluded.reason, tldr = excluded.tldr,
                points = excluded.points, skim = excluded.skim, basis = excluded.basis, language = excluded.language,
                model = excluded.model, breakdown = excluded.breakdown, minutes = excluded.minutes,
                takeaways = excluded.takeaways, created_at = CURRENT_TIMESTAMP
        """, (video_id, rating['score'], rating['verdict'], rating.get('reason', ''), rating.get('tldr', ''),
              json.dumps(rating.get('points') or [], ensure_ascii=False),
              json.dumps(rating.get('skim') or [], ensure_ascii=False),
              rating.get('basis'), rating.get('language'), model,
              json.dumps({**(rating.get('breakdown') or {}), **({"calc": rating["calc"]} if rating.get("calc") else {})},
                         ensure_ascii=False),
              json.dumps(rating.get('minutes') or [], ensure_ascii=False),
              json.dumps(rating.get('takeaways') or [], ensure_ascii=False)))
        _reindex(conn.cursor(), [video_id], kinds=("summary",))
        conn.commit()

# --- Search (Phase 1, F3) ------------------------------------------------------------
# search_docs holds every searchable piece of text (title, description, chapter, ~30 s transcript
# block, notes, AI summary) with the second it belongs to. search_index is an FTS5 index over it,
# kept in step by triggers. The trigram tokenizer matches parts of words in any script (Hindi too)
# but needs at least 3 characters per term.

SEARCH_KINDS = ("title", "chapter", "summary", "notes", "slide", "description", "transcript")
_KIND_WEIGHT = {"title": 3.0, "chapter": 2.0, "notes": 2.0, "summary": 1.5, "slide": 1.5, "description": 1.0, "transcript": 1.0}
_BLOCK_SECONDS = 30
_HIT_START, _HIT_END = "\x02", "\x03"   # snippet markers; the client escapes text, then highlights

def _create_search_tables(cursor: sqlite3.Cursor) -> bool:
    """Creates the search tables; True if they were new (so the caller fills them)."""
    exists = cursor.execute("SELECT 1 FROM sqlite_master WHERE name = 'search_docs'").fetchone()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS search_docs (
            id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, kind TEXT NOT NULL, t REAL, text TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_search_docs_video ON search_docs(video_id, kind);
        CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
            text, content='search_docs', content_rowid='id', tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
            INSERT INTO search_index(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
            INSERT INTO search_index(search_index, rowid, text) VALUES ('delete', old.id, old.text); END;
        -- Phase 2: embedding vectors keyed by model + text hash, so re-saved but unchanged text keeps its vector
        CREATE TABLE IF NOT EXISTS embedding_cache (
            model TEXT NOT NULL, hash TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL,
            PRIMARY KEY (model, hash));
    """)
    if "hash" not in _columns(cursor, "search_docs"):
        cursor.execute("ALTER TABLE search_docs ADD COLUMN hash TEXT")
        cursor.executemany("UPDATE search_docs SET hash = ? WHERE id = ?",
                           [(_doc_hash(kind, text), i) for i, kind, text in
                            cursor.execute("SELECT id, kind, text FROM search_docs").fetchall()])
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_docs_hash ON search_docs(hash)")
    return not exists

def _doc_hash(kind: str, text: str) -> str:
    import hashlib
    return hashlib.sha1(f"{kind}\x00{text}".encode("utf-8")).hexdigest()

def _docs_for(cursor: sqlite3.Cursor, video_id: str, kind: str) -> List[tuple]:
    """(t, text) rows of one kind for one video."""
    if kind == "title":
        r = cursor.execute("SELECT title, channel FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        return [(None, f"{r[0]} · {r[1] or ''}")] if r else []
    if kind == "notes":
        r = cursor.execute("SELECT notes FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        return [(None, r[0])] if r and (r[0] or "").strip() else []
    if kind in ("description", "chapter"):
        r = cursor.execute("SELECT description, chapters FROM video_details WHERE video_id = ?", (video_id,)).fetchone()
        if not r:
            return []
        if kind == "description":
            return [(None, r[0])] if (r[0] or "").strip() else []
        return [(c.get("start"), c.get("title")) for c in json.loads(r[1] or "[]") if c.get("title")]
    if kind == "transcript":
        blocks: List[list] = []
        source = _active_source(cursor, video_id)
        for start, text in cursor.execute(
                "SELECT start_s, text FROM transcript_segments WHERE video_id = ? AND source = ? ORDER BY start_s",
                (video_id, source)).fetchall():
            if blocks and start - blocks[-1][0] < _BLOCK_SECONDS:
                blocks[-1][1] += " " + text
            else:
                blocks.append([start, text])
        return [tuple(b) for b in blocks]
    if kind == "slide":
        return [(t, " · ".join(x for x in (text, visual) if (x or "").strip()))
                for t, text, visual in cursor.execute(
                    "SELECT t_s, text, visual FROM visual_segments WHERE video_id = ? ORDER BY t_s", (video_id,)).fetchall()
                if (text or "").strip() or (visual or "").strip()]
    if kind == "summary":
        r = cursor.execute("SELECT reason, tldr, points, minutes, takeaways FROM video_ratings WHERE video_id = ?",
                           (video_id,)).fetchone()
        if not r:
            return _summary_rows(cursor, video_id, [])
        rows = [(None, " ".join(x for x in (r[1], r[0]) if x))]
        minutes = json.loads(r[3] or "[]")
        if minutes:   # newer ratings: one piece per minute topic, with its notes
            rows += [(m.get("t"), ": ".join(x for x in (m.get("topic"), " ".join(m.get("notes") or [])) if x)) for m in minutes]
        else:
            rows += [(p.get("t"), p.get("text")) for p in json.loads(r[2] or "[]") if p.get("text")]
        rows += [(None, t) for t in json.loads(r[4] or "[]") if t]
        return _summary_rows(cursor, video_id, rows)
    raise ValueError(kind)

def _summary_rows(cursor: sqlite3.Cursor, video_id: str, rows: List[tuple]) -> List[tuple]:
    """Adds a Phase 3 summary (TL;DR, minutes, claims, takeaways) to the 'summary' search pieces."""
    s = cursor.execute("SELECT tldr, minutes, claims, takeaways FROM video_summaries WHERE video_id = ?", (video_id,)).fetchone()
    if s:
        rows = rows + [(None, s[0])]
        rows += [(m.get("t"), ": ".join(x for x in (m.get("topic"), " ".join(m.get("notes") or [])) if x)) for m in json.loads(s[1] or "[]")]
        rows += [(c.get("t"), c.get("text")) for c in json.loads(s[2] or "[]")]
        rows += [(None, t) for t in json.loads(s[3] or "[]")]
    return [x for x in rows if (x[1] or "").strip()]

def _reindex(cursor: sqlite3.Cursor, video_ids: Iterable[str], kinds: Iterable[str] = SEARCH_KINDS) -> None:
    kinds = tuple(kinds)
    marks = ",".join("?" * len(kinds))
    # Row ids are reused after deletes, so counts and max id can't tell that text changed; this counter can
    cursor.execute("INSERT INTO meta (key, value) VALUES ('index', 1) ON CONFLICT(key) DO UPDATE SET value = value + 1")
    for vid in video_ids:
        cursor.execute(f"DELETE FROM search_docs WHERE video_id = ? AND kind IN ({marks})", (vid, *kinds))
        for kind in kinds:
            cursor.executemany("INSERT INTO search_docs (video_id, kind, t, text, hash) VALUES (?, ?, ?, ?, ?)",
                               [(vid, kind, t, text, _doc_hash(kind, text)) for t, text in _docs_for(cursor, vid, kind)])

def rebuild_search_index(db_path: str = DB_PATH) -> int:
    """Rebuilds the whole index; returns the number of indexed pieces."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM search_docs")
        _reindex(cursor, [r[0] for r in cursor.execute("SELECT video_id FROM videos").fetchall()])
        conn.commit()
        return cursor.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]

def _crop(marked: str, width: int = 200) -> str:
    """Cuts a window of about `width` characters around the first highlighted match, on word boundaries."""
    text = " ".join((marked or "").split())
    if len(text) <= width:
        return text
    first = max(0, text.find(_HIT_START))
    start = max(0, first - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    if start > 0:
        start = text.find(" ", start) + 1 or start
    if end < len(text):
        end = text.rfind(" ", start, end) if text.rfind(" ", start, end) > first else end
    window = text[start:end]
    # A cut must not leave a highlight open
    if window.count(_HIT_START) > window.count(_HIT_END):
        window += _HIT_END
    return ("…" if start > 0 else "") + window + ("…" if end < len(text) else "")

def _fts_query(q: str) -> Optional[str]:
    """User text -> FTS5 query: "quoted phrases" and words, all required. Terms under 3 characters are dropped."""
    import re
    terms = [a or b.replace('"', '') for a, b in re.findall(r'"([^"]+)"|(\S+)', q or "")]
    terms = [t.strip() for t in terms if len(t.strip()) >= 3]
    return " ".join('"' + t.replace('"', '""') + '"' for t in terms) or None

PHRASE_BOOST = 4   # a piece containing the typed words as one phrase outranks pieces with the words apart

def search(q: str, kinds: Optional[Iterable[str]] = None, limit: int = 50, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Full-text search. Returns {'results': [{'video_id', 'score', 'hits': [{'kind', 't', 'snippet'}]}], 'short': bool}.
    Snippets mark matches with \\x02 ... \\x03. 'short' means the query had no term of 3+ characters.
    """
    init_db(db_path)
    kinds = [k for k in (kinds or SEARCH_KINDS) if k in SEARCH_KINDS]
    query = _fts_query(q)
    # Several words typed without quotes also match as one phrase, ranked first; the phrase keeps short
    # words like "no" that the word-by-word part has to drop (the index matches 3+ characters)
    phrase = " ".join((q or "").replace('"', " ").split()).lower()
    use_phrase = len(phrase) >= 3 and " " in phrase and '"' not in (q or "")
    if use_phrase:
        quoted = '"' + phrase.replace('"', '""') + '"'
        query = f"{quoted} OR ({query})" if query else quoted
    if not query or not kinds:
        return {"results": [], "short": bool((q or "").strip())}
    marks = ",".join("?" * len(kinds))
    with get_connection(db_path) as conn:
        try:
            rows = conn.execute(f"""
                SELECT d.video_id, d.kind, d.t, d.text,
                       highlight(search_index, 0, '{_HIT_START}', '{_HIT_END}') AS snip,
                       bm25(search_index) AS rank
                FROM search_index JOIN search_docs d ON d.id = search_index.rowid
                WHERE search_index MATCH ? AND d.kind IN ({marks})
                ORDER BY rank LIMIT 600
            """, (query, *kinds)).fetchall()
        except sqlite3.OperationalError:
            return {"results": [], "short": False}
    by_video: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        exact = use_phrase and phrase in " ".join((r["text"] or "").lower().split())
        score = -r["rank"] * _KIND_WEIGHT[r["kind"]] * (PHRASE_BOOST if exact else 1)
        v = by_video.setdefault(r["video_id"], {"video_id": r["video_id"], "score": 0.0, "hits": [], "matches": 0, "exact": False})
        v["matches"] += 1
        v["exact"] = v["exact"] or exact
        v["score"] = max(v["score"], score)
        if len(v["hits"]) < 4:
            v["hits"].append({"kind": r["kind"], "t": r["t"], "snippet": _crop(r["snip"])})
    results = sorted(by_video.values(), key=lambda v: (not v["exact"], -(v["score"] + 0.05 * v["matches"])))[:limit]
    for v in results:
        v["hits"].sort(key=lambda h: (SEARCH_KINDS.index(h["kind"]), h["t"] if h["t"] is not None else -1))
    return {"results": results, "short": False}

# --- Transcript sources and local transcription (Phase 2.5 L1) ------------------------
# A video can have YouTube captions and a local Whisper transcript side by side. The user picks
# which one is active per video (video_details.transcript_source); NULL means captions when they
# exist, else the local one. Everything that reads a transcript goes through _active_source.

TRANSCRIPT_SOURCES = ("captions", "local-asr")

def _sources(conn, video_id: str) -> List[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM transcript_segments WHERE video_id = ? ORDER BY source", (video_id,))]

def _active_source(conn, video_id: str) -> str:
    have = _sources(conn, video_id)
    row = conn.execute("SELECT transcript_source FROM video_details WHERE video_id = ?", (video_id,)).fetchone()
    chosen = row[0] if row else None
    if chosen in have:
        return chosen
    if "captions" in have and "local-asr" in have and _captions_wrong(conn, video_id):
        return "local-asr"   # wrong-script auto-captions: the local transcript, unless the user picked captions
    return "captions" if "captions" in have or not have else have[0]

def _captions_wrong(conn, video_id: str) -> bool:
    r = conn.execute("SELECT v.title, d.description, d.caption_kind FROM videos v JOIN video_details d ON d.video_id = v.video_id "
                     "WHERE v.video_id = ?", (video_id,)).fetchone()
    if not r:
        return False
    sample = " ".join(t for (t,) in conn.execute(
        "SELECT text FROM transcript_segments WHERE video_id = ? AND source = 'captions' ORDER BY start_s LIMIT 40", (video_id,)))
    return captions_look_wrong(sample, r[0] or "", r[1] or "", r[2] or "")

def set_transcript_source(video_id: str, source: str, db_path: str = DB_PATH) -> None:
    """Makes captions or the local transcript the one search, ratings and summaries use."""
    if source not in TRANSCRIPT_SOURCES:
        raise ValueError(f"Invalid transcript source: {source}")
    init_db(db_path)
    with get_connection(db_path) as conn:
        if source not in _sources(conn, video_id):
            raise ValueError(f"This video has no {source} transcript")
        conn.execute("UPDATE video_details SET transcript_source = ? WHERE video_id = ?", (source, video_id))
        _reindex(conn.cursor(), [video_id], kinds=("transcript",))
        conn.commit()

def save_local_transcript(video_id: str, segments: List[Dict[str, Any]], language: str = "", model: str = "",
                          db_path: str = DB_PATH) -> None:
    """
    Stores a Whisper transcript next to any captions. With no captions it becomes the active
    transcript; with captions the user's choice stands (decision 2026-10-21: keep both, user picks).
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO video_details (video_id, status) VALUES (?, 'no_captions')", (video_id,))
        cursor.execute("DELETE FROM transcript_segments WHERE video_id = ? AND source = 'local-asr'", (video_id,))
        cursor.executemany(
            "INSERT INTO transcript_segments (video_id, start_s, dur_s, text, source) VALUES (?, ?, ?, ?, 'local-asr')",
            [(video_id, seg['start'], seg.get('dur'), seg['text']) for seg in segments])
        cursor.execute("UPDATE video_details SET asr_language = ?, asr_model = ?, asr_at = CURRENT_TIMESTAMP "
                       "WHERE video_id = ?", (language, model, video_id))
        _reindex(cursor, [video_id], kinds=("transcript",))
        conn.commit()


def _script_share(text: str) -> float:
    """Share of letters that are Latin (0..1); 1.0 when there are no letters."""
    import unicodedata
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    latin = sum(1 for c in letters if "LATIN" in unicodedata.name(c, ""))
    return latin / len(letters)

@functools.lru_cache(maxsize=8192)   # pure function of its text; get_library calls it for every video on every load
def captions_look_wrong(caption_text: str, title: str, description: str = "", caption_kind: str = "") -> bool:
    """
    Auto-captions in a non-Latin script for a video whose title and description are in Latin script:
    the spike found English speech auto-captioned in Devanagari this way. When a local transcript exists, it is
    used instead (decided 2026-10-25); the captions are kept and the user can switch back per video.
    """
    if caption_kind != "auto" or not caption_text:
        return False
    return _script_share(caption_text[:2000]) < 0.3 and _script_share(f"{title} {description[:500]}") > 0.8

def videos_needing_asr(video_ids: Optional[Iterable[str]] = None, max_tries: int = 3,
                       db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Candidates for local transcription with the reason: 'no_captions' or 'wrong_script'.
    Without video_ids: videos whose details were fetched, that have no local transcript yet, and
    whose last job hasn't failed max_tries times. With video_ids: those videos (an explicit request),
    as long as they aren't unavailable or music. Not-done videos come first.
    """
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT v.video_id, v.title, v.category, v.status, v.duration_seconds,
                   d.status AS details_status, d.caption_kind, d.description,
                   EXISTS(SELECT 1 FROM transcript_segments s WHERE s.video_id = v.video_id AND s.source = 'local-asr') AS has_local,
                   j.status AS job_status, j.tries AS job_tries
            FROM videos v JOIN video_details d ON d.video_id = v.video_id
            LEFT JOIN media_jobs j ON j.video_id = v.video_id AND j.kind = 'asr'
            WHERE d.status IN ('done', 'no_captions')
            ORDER BY CASE WHEN v.status IN ('completed', 'skipped') THEN 1 ELSE 0 END, v.added_at, v.rowid
        """).fetchall()
        wanted = set(video_ids) if video_ids is not None else None
        out = []
        for r in rows:
            v = dict(r)
            if wanted is not None:
                if v["video_id"] not in wanted:
                    continue
            elif v["has_local"] or (v["job_status"] == "failed" and (v["job_tries"] or 0) >= max_tries):
                continue
            if v["details_status"] == "no_captions":
                v["reason"] = "no_captions"
            else:
                sample = " ".join(t for (t,) in conn.execute(
                    "SELECT text FROM transcript_segments WHERE video_id = ? AND source = 'captions' ORDER BY start_s LIMIT 40",
                    (v["video_id"],)))
                v["reason"] = "wrong_script" if captions_look_wrong(sample, v["title"], v["description"] or "", v["caption_kind"] or "") else None
            if v["reason"] or wanted is not None:
                out.append(v)
        return out


def set_job(video_id: str, kind: str, db_path: str = DB_PATH, **fields: Any) -> None:
    """Creates or updates a media job (status, error, progress, timings, started/finished)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO media_jobs (video_id, kind, status) VALUES (?, ?, 'queued')", (video_id, kind))
        if fields.get("status") == "failed":
            conn.execute("UPDATE media_jobs SET tries = tries + 1 WHERE video_id = ? AND kind = ?", (video_id, kind))
        if fields.get("status") in ("downloading",):
            fields.setdefault("started_at", "now")
        if fields.get("status") in ("done", "failed"):
            fields.setdefault("finished_at", "now")
        sets, params = [], []
        for k, v in fields.items():
            if k not in ("status", "error", "progress", "timings", "started_at", "finished_at"):
                raise ValueError(k)
            if k in ("started_at", "finished_at") and v == "now":
                sets.append(f"{k} = CURRENT_TIMESTAMP")
                continue
            sets.append(f"{k} = ?")
            params.append(json.dumps(v) if k == "timings" else v)
        if sets:
            conn.execute(f"UPDATE media_jobs SET {', '.join(sets)} WHERE video_id = ? AND kind = ?", (*params, video_id, kind))
        conn.commit()

# --- Slides, whiteboards and on-screen code (Phase 2.5 L2) ------------------------------
# A video is read when the user marks it (videos.has_visuals = 1) or its category's "Slides" switch
# is on (settings["visuals"]). The app only *suggests* marks (visual_hints).

import re as _re
_VISUAL_TITLE = _re.compile(r"\b(lecture|slides?|presentation|whiteboard|walk-?through|diagram|architecture|"
                            r"system design|course|crash course|explained|deep dive|tutorial|live coding|step by step)\b", _re.I)
_VISUAL_PHRASES = ("this slide", "next slide", "as you can see", "on the screen", "on my screen", "on the whiteboard",
                   "this diagram", "let me draw", "if you look at")
_visual_phrase_cache_lock = threading.Lock()
_visual_phrase_cache: Dict[str, Any] = {"key": None, "phrases": {}}

def visual_hints(db_path: str = DB_PATH) -> Dict[str, str]:
    """Why a video probably has slides/whiteboard/code worth reading: {video_id: reason}. Only a suggestion."""
    init_db(db_path)
    out: Dict[str, str] = {}
    cache_key = (db_path, index_version(db_path))
    with _visual_phrase_cache_lock:
        if _visual_phrase_cache["key"] != cache_key:
            with get_connection(db_path) as conn:
                like = " OR ".join("lower(text) LIKE ?" for _ in _VISUAL_PHRASES)
                phrases = dict(conn.execute(
                    f"SELECT video_id, COUNT(*) FROM transcript_segments WHERE {like} GROUP BY video_id",
                    [f"%{p}%" for p in _VISUAL_PHRASES]).fetchall())
            _visual_phrase_cache.update(key=cache_key, phrases=phrases)
        phrases = _visual_phrase_cache["phrases"]
    with get_connection(db_path) as conn:
        for vid, title, has, status in conn.execute(
                "SELECT v.video_id, v.title, v.has_visuals, d.status FROM videos v "
                "LEFT JOIN video_details d ON d.video_id = v.video_id"):
            if has is not None or status == "unavailable":
                continue   # the user already decided, or YouTube won't serve it
            if phrases.get(vid, 0) >= 2:
                out[vid] = f"the speaker refers to the screen {phrases[vid]} times"
            elif _VISUAL_TITLE.search(title or ""):
                out[vid] = f"title says “{_VISUAL_TITLE.search(title).group(0)}”"
    return out

def set_has_visuals(video_ids: Iterable[str], value: Optional[bool], db_path: str = DB_PATH) -> int:
    """Marks videos as having (True) or not having (False) slides worth reading; None clears the mark."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute("UPDATE videos SET has_visuals = ? WHERE video_id IN (SELECT value FROM json_each(?))",
                           (None if value is None else int(bool(value)), json.dumps(list(video_ids))))
        conn.commit()
        return cur.rowcount

def videos_needing_visuals(video_ids: Optional[Iterable[str]] = None, visual_categories: Iterable[str] = (),
                           max_tries: int = 3, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Videos to read slides from: marked by the user, or in a category whose "Slides" switch is on
    (unless the video is marked 0), not yet read, and not failed max_tries times. With video_ids:
    exactly those (an explicit request). Not-done videos first.
    """
    init_db(db_path)
    cats = set(visual_categories)
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT v.video_id, v.title, v.category, v.status, v.duration_seconds, v.has_visuals,
                   d.status AS details_status,
                   EXISTS(SELECT 1 FROM visual_segments s WHERE s.video_id = v.video_id) AS has_slides,
                   j.status AS job_status, j.tries AS job_tries
            FROM videos v LEFT JOIN video_details d ON d.video_id = v.video_id
            LEFT JOIN media_jobs j ON j.video_id = v.video_id AND j.kind = 'visual'
            ORDER BY CASE WHEN v.status IN ('completed', 'skipped') THEN 1 ELSE 0 END, v.added_at, v.rowid
        """).fetchall()
    wanted = set(video_ids) if video_ids is not None else None
    out = []
    for r in rows:
        v = dict(r)
        if v["details_status"] == "unavailable":
            continue
        if wanted is not None:
            if v["video_id"] in wanted:
                out.append(v)
            continue
        marked = v["has_visuals"] == 1 or (v["has_visuals"] is None and v["category"] in cats)
        done = v["has_slides"] or v["job_status"] == "done"
        gave_up = v["job_status"] == "failed" and (v["job_tries"] or 0) >= max_tries
        if marked and not done and not gave_up:
            out.append(v)
    return out

def save_visuals(video_id: str, frames: List[Dict[str, Any]], db_path: str = DB_PATH) -> None:
    """Replaces a video's slide segments: [{'t', 'text', 'visual', 'thumb'}] (frames without content excluded)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM visual_segments WHERE video_id = ?", (video_id,))
        cursor.executemany("INSERT INTO visual_segments (video_id, t_s, text, visual, thumb) VALUES (?, ?, ?, ?, ?)",
                           [(video_id, f["t"], f.get("text") or "", f.get("visual") or "", f.get("thumb")) for f in frames])
        _reindex(cursor, [video_id], kinds=("slide",))
        conn.commit()

def get_visuals(video_id: str, db_path: str = DB_PATH, conn=None) -> List[Dict[str, Any]]:
    if conn is None:
        init_db(db_path)
        with get_connection(db_path) as c:
            return get_visuals(video_id, db_path, c)
    return [dict(r) for r in conn.execute(
        "SELECT t_s AS t, text, visual, thumb FROM visual_segments WHERE video_id = ? ORDER BY t_s", (video_id,))]

# --- Embeddings (Phase 2) -----------------------------------------------------------------

def docs_missing_embeddings(model: str, limit: Optional[int] = None, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Search pieces whose text has no vector for this model yet: [{'hash', 'kind', 'text'}], one per distinct text."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute(f"""
            SELECT d.hash, MIN(d.kind) AS kind, MIN(d.text) AS text FROM search_docs d
            WHERE d.hash IS NOT NULL AND NOT EXISTS (SELECT 1 FROM embedding_cache e WHERE e.model = ? AND e.hash = d.hash)
            GROUP BY d.hash {'LIMIT ' + str(int(limit)) if limit else ''}
        """, (model,)).fetchall()
        return [dict(r) for r in rows]

def save_embeddings(model: str, items: List[tuple], db_path: str = DB_PATH) -> None:
    """items: [(hash, vector as float32 bytes, dim)]."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.executemany("INSERT OR REPLACE INTO embedding_cache (model, hash, dim, vector) VALUES (?, ?, ?, ?)",
                         [(model, h, dim, blob) for h, blob, dim in items])
        conn.commit()

def prune_embeddings(model: Optional[str] = None, db_path: str = DB_PATH) -> int:
    """Drops vectors whose text is no longer in the search index (and, with model, vectors of other models)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        n = conn.execute("DELETE FROM embedding_cache WHERE hash NOT IN (SELECT hash FROM search_docs WHERE hash IS NOT NULL)").rowcount
        if model:
            n += conn.execute("DELETE FROM embedding_cache WHERE model != ?", (model,)).rowcount
        conn.commit()
        return n

def clear_embeddings(model: str, db_path: str = DB_PATH) -> int:
    """Drops every vector of a model, so the next update embeds the whole library again (Rebuild)."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        n = conn.execute("DELETE FROM embedding_cache WHERE model = ?", (model,)).rowcount
        conn.commit()
        return n

def embedding_status(model: str, db_path: str = DB_PATH) -> Dict[str, int]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(DISTINCT hash) FROM search_docs WHERE hash IS NOT NULL").fetchone()[0]
        done = conn.execute("SELECT COUNT(DISTINCT d.hash) FROM search_docs d JOIN embedding_cache e "
                            "ON e.hash = d.hash AND e.model = ?", (model,)).fetchone()[0]
        return {"pieces": total, "embedded": done}

def embedded_docs(model: str, db_path: str = DB_PATH) -> tuple:
    """(rows [{'id', 'video_id', 'kind', 't', 'text'}], list of float32 vector bytes) for every piece with a vector."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT d.id, d.video_id, d.kind, d.t, d.text, e.vector FROM search_docs d
            JOIN embedding_cache e ON e.hash = d.hash AND e.model = ? ORDER BY d.id
        """, (model,)).fetchall()
    return [{k: r[k] for k in ("id", "video_id", "kind", "t", "text")} for r in rows], [r["vector"] for r in rows]

def index_version(db_path: str = DB_PATH) -> tuple:
    """Changes whenever search pieces or vectors change; used to refresh in-memory caches."""
    with get_connection(db_path) as conn:
        return conn.execute("SELECT (SELECT value FROM meta WHERE key = 'index'), (SELECT COUNT(*) FROM search_docs), "
                            "(SELECT COUNT(*) FROM embedding_cache)").fetchone()[:]


# --- Questions and answers (Phase 2) ------------------------------------------------------

_ASK_JSON = ("citations", "sources", "filters")

def _ask_row(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k in _ASK_JSON:
        d[k] = json.loads(d[k] or ("{}" if k == "filters" else "[]"))
    return d

def save_ask(item: Dict[str, Any], thread_id: Optional[int] = None, db_path: str = DB_PATH) -> int:
    """Keeps one answered question; a follow-up joins thread_id (the first question's id). Returns its id."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO ask_history (question, answer, citations, sources, filters, mode, model, seconds, thread_id, standalone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item["question"], item.get("answer", ""), *(json.dumps(item.get(k) or ({} if k == "filters" else []), ensure_ascii=False) for k in _ASK_JSON),
             item.get("mode"), item.get("model"), item.get("seconds"), thread_id, item.get("standalone")))
        if thread_id is None:
            conn.execute("UPDATE ask_history SET thread_id = id WHERE id = ?", (cur.lastrowid,))
        conn.commit()
        return cur.lastrowid

def list_asks(q: str = "", limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Conversations, newest activity first: [{'id' (= thread id), 'question' (the first one), 'answer', 'follow_ups',
    'filters', 'created_at'}]. q matches any question or answer in the conversation.
    """
    init_db(db_path)
    like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT r.id, r.question, r.answer, r.filters, r.created_at, COUNT(a.id) - 1 AS follow_ups, MAX(a.id) AS last
            FROM ask_history r JOIN ask_history a ON a.thread_id = r.id
            WHERE r.thread_id = r.id AND r.id IN (SELECT thread_id FROM ask_history
                WHERE question LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\')
            GROUP BY r.id ORDER BY last DESC LIMIT ?""", (like, like, int(limit))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("last")
            d["filters"] = json.loads(d["filters"] or "{}")
            out.append(d)
        return out

def get_ask(ask_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        r = conn.execute("SELECT * FROM ask_history WHERE id = ?", (int(ask_id),)).fetchone()
        return _ask_row(r) if r else None

def get_thread(ask_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Every question and answer of the conversation that ask_id belongs to, oldest first."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM ask_history WHERE thread_id = (SELECT thread_id FROM ask_history WHERE id = ?) "
                            "ORDER BY id", (int(ask_id),)).fetchall()
        return [_ask_row(r) for r in rows]

def delete_ask(ask_id: int, db_path: str = DB_PATH) -> int:
    """Deletes the whole conversation ask_id belongs to; returns how many answers went."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        n = conn.execute("DELETE FROM ask_history WHERE thread_id = (SELECT thread_id FROM ask_history WHERE id = ?)",
                         (int(ask_id),)).rowcount
        conn.commit()
        return n


# --- Libraries ------------------------------------------------------------------------------

def get_libraries(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """[{'id', 'name', 'playlists'}], library 1 first."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute("""
            SELECT l.id, l.name, COUNT(p.id) AS playlists FROM libraries l LEFT JOIN playlists p ON p.library_id = l.id
            GROUP BY l.id ORDER BY l.id""")]

def _library_name(name: Any) -> str:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("Give the library a name")
    return name

def create_library(name: str, db_path: str = DB_PATH) -> int:
    init_db(db_path)
    with get_connection(db_path) as conn:
        try:
            cur = conn.execute("INSERT INTO libraries (name) VALUES (?)", (_library_name(name),))
        except sqlite3.IntegrityError:
            raise ValueError("A library with that name already exists") from None
        conn.commit()
        return cur.lastrowid

def rename_library(library_id: int, name: str, db_path: str = DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        try:
            n = conn.execute("UPDATE libraries SET name = ? WHERE id = ?", (_library_name(name), int(library_id))).rowcount
        except sqlite3.IntegrityError:
            raise ValueError("A library with that name already exists") from None
        if not n:
            raise ValueError("Unknown library")
        conn.commit()

def delete_library(library_id: int, db_path: str = DB_PATH) -> None:
    """Deletes an empty library (move its playlists first). Library 1 always stays."""
    init_db(db_path)
    if int(library_id) == 1:
        raise ValueError("The first library can't be deleted; rename it instead")
    with get_connection(db_path) as conn:
        if conn.execute("SELECT 1 FROM playlists WHERE library_id = ?", (int(library_id),)).fetchone():
            raise ValueError("Move this library's playlists to another library first")
        if not conn.execute("DELETE FROM libraries WHERE id = ?", (int(library_id),)).rowcount:
            raise ValueError("Unknown library")
        conn.commit()

def set_playlist_library(playlist_id: str, library_id: int, db_path: str = DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        if not conn.execute("SELECT 1 FROM libraries WHERE id = ?", (int(library_id),)).fetchone():
            raise ValueError("Unknown library")
        if not conn.execute("UPDATE playlists SET library_id = ? WHERE id = ?", (int(library_id), playlist_id)).rowcount:
            raise ValueError("Unknown playlist")
        conn.commit()

def playlist_library(playlist_id: str, db_path: str = DB_PATH) -> Optional[int]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        r = conn.execute("SELECT library_id FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return r[0] if r else None

def video_libraries(db_path: str = DB_PATH) -> Dict[str, int]:
    """{video_id: library} for every video (see _video_library)."""
    return {v['video_id']: v['library'] for v in get_library(db_path)}


# --- Summaries (Phase 3) ---------------------------------------------------------------------

_SUMMARY_JSON = {"minutes": "[]", "claims": "[]", "quiz": "[]", "takeaways": "[]", "learn_more": "{}"}

def save_summary(video_id: str, summary: Dict[str, Any], model: str = "", db_path: str = DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(f"""
            INSERT OR REPLACE INTO video_summaries (video_id, tldr, {", ".join(_SUMMARY_JSON)}, model, language, created_at)
            VALUES (?, ?, {", ".join("?" * len(_SUMMARY_JSON))}, ?, ?, CURRENT_TIMESTAMP)""",
            (video_id, summary.get("tldr", ""), *(json.dumps(summary.get(k) or json.loads(d), ensure_ascii=False) for k, d in _SUMMARY_JSON.items()),
             model, summary.get("language")))
        _reindex(conn.cursor(), [video_id], kinds=("summary",))
        conn.commit()

def get_summary(video_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        r = conn.execute("SELECT * FROM video_summaries WHERE video_id = ?", (video_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k, default in _SUMMARY_JSON.items():
        d[k] = json.loads(d[k] or default)
    return d

def summarized_ids(db_path: str = DB_PATH) -> set:
    return set(summary_times(db_path))

def summary_times(db_path: str = DB_PATH) -> Dict[str, str]:
    """{video_id: when its summary was made} for every summarised video."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        return dict(conn.execute("SELECT video_id, created_at FROM video_summaries").fetchall())

# --- Knowledge pages (Phase 3) -------------------------------------------------------------

def save_page(library_id: int, category: str, page: Dict[str, Any], sources: List[Dict[str, Any]], video_ids_hash: str,
              model: str = "", db_path: str = DB_PATH) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO topic_pages (library_id, category, content, sources, video_ids_hash, model, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                     (int(library_id), category, json.dumps(page, ensure_ascii=False), json.dumps(sources, ensure_ascii=False),
                      video_ids_hash, model))
        conn.commit()

def get_page(library_id: int, category: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """{'page', 'sources', 'video_ids_hash', 'model', 'created_at'} or None."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        r = conn.execute("SELECT * FROM topic_pages WHERE library_id = ? AND category = ?", (int(library_id), category)).fetchone()
    if not r:
        return None
    return {"page": {**json.loads(r["content"] or "{}"), "created_at": r["created_at"]}, "sources": json.loads(r["sources"] or "[]"),
            "video_ids_hash": r["video_ids_hash"], "model": r["model"], "created_at": r["created_at"]}

def list_pages(library_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT category, video_ids_hash, model, created_at FROM topic_pages WHERE library_id = ?",
                                              (int(library_id),))]
