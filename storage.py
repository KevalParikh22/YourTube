"""
Database storage layer using SQLite for local, persistent storage.
Tracks playlists, videos, playlist membership, categories, and watch completion statuses.

Membership lives in `playlist_items` so one video can belong to several playlists, and
videos that disappear from a source playlist are flagged (`removed = 1`) rather than deleted.
Manual category assignments are protected by `videos.category_locked` and survive re-ingestion.
"""
import sqlite3
import os
import json
from typing import List, Dict, Any, Optional, Callable, Iterable

DB_PATH = os.environ.get("YTKB_DB") or os.path.join(os.path.dirname(__file__), "yt_kb.db")
VALID_STATUSES = ("unwatched", "in_progress", "completed")
FALLBACK_CATEGORY = "General / Other"

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _columns(cursor: sqlite3.Cursor, table: str) -> set:
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                playlist_id TEXT,
                title TEXT NOT NULL,
                channel TEXT,
                duration_seconds INTEGER DEFAULT 0,
                url TEXT NOT NULL,
                category TEXT DEFAULT 'Uncategorized',
                status TEXT CHECK(status IN ('unwatched', 'in_progress', 'completed')) DEFAULT 'unwatched',
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                category_locked INTEGER NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                status_updated_at TIMESTAMP,
                FOREIGN KEY(playlist_id) REFERENCES playlists(id)
            )
        """)

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

        # Migrate databases created before these columns existed
        existing = _columns(cursor, "videos")
        for col, ddl in (
            ("category_locked", "INTEGER NOT NULL DEFAULT 0"),
            ("notes", "TEXT DEFAULT ''"),
            ("status_updated_at", "TIMESTAMP"),
        ):
            if col not in existing:
                cursor.execute(f"ALTER TABLE videos ADD COLUMN {col} {ddl}")

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
    for v in videos.values():
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
        sets.append("category = ?, category_locked = 1")
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
        conn.commit()
        return cursor.rowcount

def update_video_status(video_id: str, status: str, db_path: str = DB_PATH) -> None:
    """Updates watch status: unwatched, in_progress, completed."""
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
                cursor.execute("UPDATE videos SET category_locked = 0 WHERE video_id IN (SELECT value FROM json_each(?))", (ids,))
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
        conn.commit()
        return cursor.rowcount

def release_category(name: str, db_path: str = DB_PATH) -> int:
    """Moves a deleted category's videos to the fallback category and unlocks them."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE videos SET category = ?, category_locked = 0 WHERE category = ?",
            (FALLBACK_CATEGORY, name)
        )
        conn.commit()
        return cursor.rowcount
