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
from typing import List, Dict, Any, Optional, Callable, Iterable

DB_PATH = os.environ.get("YTKB_DB") or os.path.join(os.path.dirname(__file__), "yt_kb.db")
VALID_STATUSES = ("unwatched", "in_progress", "completed", "skipped")
# Statuses that no longer count as remaining work ("skipped" = decided not to watch)
DONE_STATUSES = ("completed", "skipped")
FALLBACK_CATEGORY = "General / Other"

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
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
            CREATE TABLE IF NOT EXISTS media_jobs (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL,
                kind TEXT NOT NULL,                  -- 'asr' now; 'visual' later (Phase 2.5 L2)
                status TEXT NOT NULL,                -- queued, downloading, transcribing, done, failed
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

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_videos_playlist ON videos(playlist_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_video ON playlist_items(video_id)")
        if _create_search_tables(cursor):
            _reindex(cursor, [r[0] for r in cursor.execute("SELECT video_id FROM videos")])
        conn.commit()

def save_playlist(playlist_info: Dict[str, Any], videos: List[Dict[str, Any]], db_path: str = DB_PATH) -> Dict[str, int]:
    """
    Saves or updates a playlist and its videos.
    Keeps existing watch status, notes and manually locked categories.
    Videos no longer present in the source are flagged removed, never deleted.
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
            INSERT INTO playlists (id, title, original_url, total_videos, total_duration_seconds, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                original_url = COALESCE(NULLIF(excluded.original_url, ''), playlists.original_url),
                total_videos = excluded.total_videos,
                total_duration_seconds = excluded.total_duration_seconds,
                updated_at = CURRENT_TIMESTAMP
        """, (
            playlist_id,
            playlist_info.get('title', 'Untitled Playlist'),
            playlist_info.get('url', ''),
            len(videos),
            sum(v.get('duration_seconds', 0) for v in videos)
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
        # An empty extraction is far more likely a fetch failure than an emptied playlist
        if current_ids:
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
        for row in cursor.execute("SELECT playlist_id, video_id, position, removed FROM playlist_items"):
            v = videos.get(row['video_id'])
            if v is not None:
                v['sources'].append({'id': row['playlist_id'], 'position': row['position'], 'removed': row['removed']})
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
        jobs = {row['video_id']: {'status': row['status'], 'error': row['error'], 'tries': row['tries'],
                                  'progress': row['progress']}
                for row in cursor.execute("SELECT video_id, status, error, tries, progress FROM media_jobs WHERE kind = 'asr'")}
        ratings = {}
        for row in cursor.execute("SELECT * FROM video_ratings"):
            r = dict(row)
            r['points'], r['skim'] = json.loads(r['points'] or '[]'), json.loads(r['skim'] or '[]')
            ratings[r.pop('video_id')] = r
    reasons = {c['video_id']: c['reason'] for c in videos_needing_asr(db_path=db_path)}
    for v in videos.values():
        if v['video_id'] in details:
            details[v['video_id']]['asr'] = jobs.get(v['video_id'])
            details[v['video_id']]['asr_reason'] = reasons.get(v['video_id'])
        v['rating'] = ratings.get(v['video_id'])
        v['suggestion'] = suggestions.get(v['video_id'])
        v['details'] = details.get(v['video_id'])
        v['in_source'] = any(not s['removed'] for s in v['sources'])
    return list(videos.values())

def get_playlists(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lists ingested playlists with live (non-removed) counts and durations."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.title, p.original_url, p.created_at, p.updated_at,
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

def update_video_status(video_id: str, status: str, db_path: str = DB_PATH) -> None:
    """Updates watch status: unwatched, in_progress, completed, skipped."""
    update_videos([video_id], status=status, db_path=db_path)

def update_video_category(video_id: str, category: str, db_path: str = DB_PATH) -> None:
    """Reclassifies a video into a specific category (locked against auto-classification)."""
    update_videos([video_id], category=category, db_path=db_path)

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

def rename_category(old: str, new: str, db_path: str = DB_PATH) -> int:
    """Renames a category on every video that carries it (lock state is preserved)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE videos SET category = ? WHERE category = ?", (new, old))
        n = cursor.rowcount
        cursor.execute("UPDATE category_suggestions SET category = ? WHERE category = ?", (new, old))
        conn.commit()
        return n

def release_category(name: str, db_path: str = DB_PATH) -> int:
    """Moves a deleted category's videos to the fallback category and unlocks them."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE videos SET category = ?, category_locked = 0, category_source = 'keywords' WHERE category = ?",
            (FALLBACK_CATEGORY, name)
        )
        n = cursor.rowcount
        cursor.execute("DELETE FROM category_suggestions WHERE category = ?", (name,))
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
            return None
        d = dict(row)
        for key in ('tags', 'chapters', 'yt_categories'):
            d[key] = json.loads(d[key] or '[]')
        d['sources'] = _sources(conn, video_id)
        d['transcript'] = _active_source(conn, video_id)
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
            INSERT INTO video_ratings (video_id, score, verdict, reason, tldr, points, skim, basis, language, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                score = excluded.score, verdict = excluded.verdict, reason = excluded.reason, tldr = excluded.tldr,
                points = excluded.points, skim = excluded.skim, basis = excluded.basis, language = excluded.language,
                model = excluded.model, created_at = CURRENT_TIMESTAMP
        """, (video_id, rating['score'], rating['verdict'], rating.get('reason', ''), rating.get('tldr', ''),
              json.dumps(rating.get('points') or [], ensure_ascii=False),
              json.dumps(rating.get('skim') or [], ensure_ascii=False),
              rating.get('basis'), rating.get('language'), model))
        _reindex(conn.cursor(), [video_id], kinds=("summary",))
        conn.commit()

# --- Search (Phase 1, F3) ------------------------------------------------------------
# search_docs holds every searchable piece of text (title, description, chapter, ~30 s transcript
# block, notes, AI summary) with the second it belongs to. search_index is an FTS5 index over it,
# kept in step by triggers. The trigram tokenizer matches parts of words in any script (Hindi too)
# but needs at least 3 characters per term.

SEARCH_KINDS = ("title", "chapter", "summary", "notes", "description", "transcript")
_KIND_WEIGHT = {"title": 3.0, "chapter": 2.0, "notes": 2.0, "summary": 1.5, "description": 1.0, "transcript": 1.0}
_BLOCK_SECONDS = 30
_HIT_START, _HIT_END = "\x02", "\x03"   # snippet markers; the client escapes text, then highlights

def _create_search_tables(cursor: sqlite3.Cursor) -> bool:
    """Creates the search tables; True if they were new (so the caller fills them)."""
    exists = cursor.execute("SELECT 1 FROM sqlite_master WHERE name = 'search_docs'").fetchone()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS search_docs (
            id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, kind TEXT NOT NULL, t REAL, text TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_search_docs_video ON search_docs(video_id, kind);
        CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
            text, content='search_docs', content_rowid='id', tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
            INSERT INTO search_index(rowid, text) VALUES (new.id, new.text); END;
        CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
            INSERT INTO search_index(search_index, rowid, text) VALUES ('delete', old.id, old.text); END;
    """)
    return not exists

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
    if kind == "summary":
        r = cursor.execute("SELECT reason, tldr, points FROM video_ratings WHERE video_id = ?", (video_id,)).fetchone()
        if not r:
            return []
        rows = [(None, " ".join(x for x in (r[1], r[0]) if x))]
        rows += [(p.get("t"), p.get("text")) for p in json.loads(r[2] or "[]") if p.get("text")]
        return [x for x in rows if (x[1] or "").strip()]
    raise ValueError(kind)

def _reindex(cursor: sqlite3.Cursor, video_ids: Iterable[str], kinds: Iterable[str] = SEARCH_KINDS) -> None:
    kinds = tuple(kinds)
    marks = ",".join("?" * len(kinds))
    for vid in video_ids:
        cursor.execute(f"DELETE FROM search_docs WHERE video_id = ? AND kind IN ({marks})", (vid, *kinds))
        for kind in kinds:
            cursor.executemany("INSERT INTO search_docs (video_id, kind, t, text) VALUES (?, ?, ?, ?)",
                               [(vid, kind, t, text) for t, text in _docs_for(cursor, vid, kind)])

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

def search(q: str, kinds: Optional[Iterable[str]] = None, limit: int = 50, db_path: str = DB_PATH) -> Dict[str, Any]:
    """
    Full-text search. Returns {'results': [{'video_id', 'score', 'hits': [{'kind', 't', 'snippet'}]}], 'short': bool}.
    Snippets mark matches with \\x02 ... \\x03. 'short' means the query had no term of 3+ characters.
    """
    init_db(db_path)
    kinds = [k for k in (kinds or SEARCH_KINDS) if k in SEARCH_KINDS]
    query = _fts_query(q)
    if not query or not kinds:
        return {"results": [], "short": bool((q or "").strip())}
    marks = ",".join("?" * len(kinds))
    with get_connection(db_path) as conn:
        try:
            rows = conn.execute(f"""
                SELECT d.video_id, d.kind, d.t,
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
        score = -r["rank"] * _KIND_WEIGHT[r["kind"]]
        v = by_video.setdefault(r["video_id"], {"video_id": r["video_id"], "score": 0.0, "hits": [], "matches": 0})
        v["matches"] += 1
        v["score"] = max(v["score"], score)
        if len(v["hits"]) < 4:
            v["hits"].append({"kind": r["kind"], "t": r["t"], "snippet": _crop(r["snip"])})
    results = sorted(by_video.values(), key=lambda v: -(v["score"] + 0.05 * v["matches"]))[:limit]
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
    return "captions" if "captions" in have or not have else have[0]

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

def captions_look_wrong(caption_text: str, title: str, description: str = "", caption_kind: str = "") -> bool:
    """
    Auto-captions in a non-Latin script for a video whose title and description are in Latin script:
    the spike found English speech auto-captioned in Devanagari this way. It's only a hint; the local
    transcript is kept next to the captions and the user chooses.
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
