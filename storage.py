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
                                     'tries': row['tries']}
                   for row in cursor.execute("SELECT video_id, status, caption_kind, caption_lang, tries, "
                                             "json_array_length(chapters) AS n_chapters FROM video_details")}
    for v in videos.values():
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
                                       caption_lang, caption_kind, tries, error, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(video_id) DO UPDATE SET
                status = excluded.status, description = excluded.description, tags = excluded.tags,
                chapters = excluded.chapters, language = excluded.language, yt_categories = excluded.yt_categories,
                caption_lang = excluded.caption_lang, caption_kind = excluded.caption_kind,
                tries = excluded.tries, error = excluded.error, fetched_at = CURRENT_TIMESTAMP
        """, (
            video_id, result['status'], result.get('description') or '',
            json.dumps(result.get('tags') or [], ensure_ascii=False),
            json.dumps(result.get('chapters') or [], ensure_ascii=False),
            result.get('language'), json.dumps(result.get('yt_categories') or [], ensure_ascii=False),
            result.get('caption_lang'), result.get('caption_kind'), tries, result.get('error'),
        ))
        if result['status'] != 'failed':
            cursor.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
            cursor.executemany(
                "INSERT INTO transcript_segments (video_id, start_s, dur_s, text) VALUES (?, ?, ?, ?)",
                [(video_id, seg['start'], seg.get('dur'), seg['text']) for seg in result.get('segments') or []])
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
        d['segments'] = [dict(r) for r in conn.execute(
            "SELECT start_s AS start, dur_s AS dur, text FROM transcript_segments WHERE video_id = ? ORDER BY start_s",
            (video_id,))]
        return d
