"""Local data files: usage, backups, clearable caches, and saved exports."""
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import date, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("YTKB_DATA_DIR") or ROOT
NAME_RE = re.compile(r"^[\w.-]{1,120}$")
FRAMES_DIR = os.path.join(DATA_DIR, "frames")   # same folder as dashboard.FRAMES_DIR; importing dashboard here would load it twice


def _areas():
    import storage
    import applog
    return [
        ("db", "Library database", storage.DB_PATH, False),
        ("frames", "Slide pictures", FRAMES_DIR, True),
        ("media", "Media cache", os.path.join(DATA_DIR, "media_cache"), True),
        ("logs", "Logs", applog.LOG_DIR, True),
        ("backups", "Backups", os.path.join(DATA_DIR, "backups"), False),
        ("exports", "Exports", os.path.join(DATA_DIR, "exports"), False),
    ]


def _walk(path):
    """Return bytes and regular-file count without traversing symlinks."""
    total = count = 0
    try:
        entries = list(os.scandir(path))
    except (FileNotFoundError, NotADirectoryError):
        return total, count
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                size, files = _walk(entry.path)
                total += size
                count += files
            elif entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
                count += 1
        except OSError:
            continue
    return total, count


def usage():
    import storage
    areas = []
    for key, label, path, clearable in _areas():
        paths = [path]
        if key == "db":
            paths += [path + "-wal", path + "-shm"]
        size = count = 0
        for item in paths:
            if os.path.isfile(item) and not os.path.islink(item):
                size += os.path.getsize(item)
                count += 1
            elif os.path.isdir(item) and not os.path.islink(item):
                n, f = _walk(item)
                size += n
                count += f
        areas.append({"key": key, "label": label, "bytes": size, "files": count, "clearable": clearable})
    with storage.get_connection() as conn:
        counts = {
            "videos": conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
            "transcripts": conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0],
            "summaries": conn.execute("SELECT COUNT(*) FROM video_summaries").fetchone()[0],
            "slides": conn.execute("SELECT COUNT(*) FROM visual_segments").fetchone()[0],
        }
    return {"data_dir": DATA_DIR, "areas": areas, "counts": counts}


def _backup_dir():
    path = os.path.join(DATA_DIR, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def _prune_backups(protect=None):
    folder = _backup_dir()
    backups = sorted((os.path.join(folder, n) for n in os.listdir(folder)
                      if re.fullmatch(r"yourtube-\d{8}-\d{6}(?:-\d+)?\.db", n) and os.path.isfile(os.path.join(folder, n))),
                     key=os.path.getmtime)
    keep = set(backups[-10:]) | ({protect} if protect else set())
    for path in backups:
        if path not in keep:
            os.remove(path)
            sibling = os.path.splitext(path)[0]
            if os.path.isdir(sibling) and not os.path.islink(sibling):
                shutil.rmtree(sibling)


def backup(note="", protect=None):
    import storage
    import classifier
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = _backup_dir()
    name = f"yourtube-{stamp}.db"
    target = os.path.join(folder, name)
    # Avoid replacing a same-second backup.
    suffix = 1
    while os.path.exists(target):
        name = f"yourtube-{stamp}-{suffix}.db"
        target = os.path.join(folder, name)
        suffix += 1
    os.makedirs(os.path.dirname(storage.DB_PATH), exist_ok=True)
    src = sqlite3.connect(storage.DB_PATH)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    taxonomy_dir = os.path.splitext(target)[0]
    os.makedirs(taxonomy_dir, exist_ok=True)
    taxonomy_folder = os.path.dirname(classifier.TAXONOMY_PATH) or "."
    stem, ext = os.path.splitext(os.path.basename(classifier.TAXONOMY_PATH))
    # library 1's file plus taxonomy-N for the others (classifier.taxonomy_path)
    library_file = re.compile(re.escape(stem) + r"(-\d+)?" + re.escape(ext) + "$")
    for item in (os.listdir(taxonomy_folder) if os.path.isdir(taxonomy_folder) else []):
        if library_file.fullmatch(item):
            src = os.path.join(taxonomy_folder, item)
            if os.path.isfile(src) and not os.path.islink(src):
                shutil.copy2(src, os.path.join(taxonomy_dir, item))
    _prune_backups(protect)
    print(f"Backed up the library to backups/{name}.")
    return name


def restore(name):
    """
    Replaces the live library with a backup. A safety backup of the current library is made first.
    The caller makes sure no task is running. Returns the safety backup's name.
    """
    import storage
    import classifier
    path = get_saved("backups", name)
    if path is None or not re.fullmatch(r"yourtube-\d{8}-\d{6}(?:-\d+)?\.db", name):
        raise FileNotFoundError(name)
    with closing(sqlite3.connect("file:" + os.path.abspath(path) + "?mode=ro", uri=True)) as src:
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("This backup is damaged; the library was not changed")
    safety = backup(protect=path)   # newest, so pruning keeps it; the chosen one is protected too
    with storage.get_connection(storage.DB_PATH) as live:
        before = live.execute("SELECT value FROM meta WHERE key = 'index'").fetchone()
    # The backup API in reverse: copies every page under one write lock, safe with the app's open connections
    with closing(sqlite3.connect("file:" + os.path.abspath(path) + "?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(storage.DB_PATH, timeout=30)) as live:
        src.backup(live)
    folder = os.path.splitext(path)[0]
    taxonomy_folder = os.path.dirname(classifier.TAXONOMY_PATH) or "."
    if os.path.isdir(folder) and not os.path.islink(folder):
        for item in os.listdir(folder):
            item_path = os.path.join(folder, item)
            if item.endswith(".json") and os.path.isfile(item_path) and not os.path.islink(item_path):
                shutil.copy2(item_path, os.path.join(taxonomy_folder, item))
    storage.init_db(storage.DB_PATH)   # migrates a backup made by an older version
    with storage.get_connection(storage.DB_PATH) as live:
        # Past both versions, so in-memory search caches always notice the swap
        live.execute("INSERT INTO meta (key, value) VALUES ('index', ?) ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value - 1) + 1",
                     ((before[0] if before else 0) + 1,))
        live.commit()
    print(f"Restored {name}. The library before it is saved as {safety}.")
    return safety


def list_backups():
    folder = os.path.join(DATA_DIR, "backups")
    if not os.path.isdir(folder):
        return []
    result = []
    for name in os.listdir(folder):
        if not re.fullmatch(r"yourtube-\d{8}-\d{6}(?:-\d+)?\.db", name):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        videos = 0
        try:
            uri = "file:" + os.path.abspath(path) + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        except sqlite3.Error:
            pass
        result.append({"name": name, "bytes": os.path.getsize(path),
                       "created": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds"),
                       "videos": videos})
    return sorted(result, key=lambda x: x["created"], reverse=True)


def _inside(path, root):
    real_path, real_root = os.path.realpath(path), os.path.realpath(root)
    return real_path == real_root or real_path.startswith(real_root + os.sep)


def clear(key):
    areas = {k: (p, c) for k, _, p, c in _areas()}
    if key not in areas or not areas[key][1]:
        raise ValueError("Unknown or non-clearable area")
    path, _ = areas[key]
    root = os.path.realpath(path)
    if os.path.islink(path) or os.path.abspath(path) != root:
        raise ValueError("Path is outside the clearable area")
    if not os.path.isdir(path) or os.path.islink(path):
        return 0
    removed = 0
    for entry in os.scandir(path):
        if key == "logs" and entry.name == "yourtube.log" and datetime.fromtimestamp(entry.stat(follow_symlinks=False).st_mtime).date() == date.today():
            continue
        if entry.is_symlink():
            os.unlink(entry.path)
        elif entry.is_dir(follow_symlinks=False):
            if not _inside(entry.path, root):
                raise ValueError("Path is outside the clearable area")
            shutil.rmtree(entry.path)
        elif entry.is_file(follow_symlinks=False):
            os.remove(entry.path)
        removed += 1
    return removed


def save_export(name, data):
    folder = os.path.join(DATA_DIR, "exports")
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", os.path.basename(name))[:120]
    if not safe or safe in (".", ".."):
        safe = "export.bin"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target_name = f"{stamp}-{safe}"
    target = os.path.join(folder, target_name)
    n = 1
    while os.path.exists(target):
        target_name = f"{stamp}-{n}-{safe}"
        target = os.path.join(folder, target_name)
        n += 1
    with open(target, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return target_name


def list_exports():
    folder = os.path.join(DATA_DIR, "exports")
    if not os.path.isdir(folder):
        return []
    items = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if NAME_RE.fullmatch(name) and os.path.isfile(path) and not os.path.islink(path):
            items.append({"name": name, "bytes": os.path.getsize(path),
                          "created": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")})
    return sorted(items, key=lambda x: x["created"], reverse=True)


def get_saved(area, name):
    if not NAME_RE.fullmatch(name or ""):
        return None
    root = os.path.join(DATA_DIR, "exports" if area == "exports" else "backups")
    path = os.path.join(root, name)
    if not _inside(path, root) or not os.path.isfile(path) or os.path.islink(path):
        return None
    return path


def delete(area, name):
    if area not in ("exports", "backups") or not NAME_RE.fullmatch(name or ""):
        raise ValueError("Invalid area or file name")
    path = get_saved(area, name)
    if path is None:
        raise FileNotFoundError(name)
    os.remove(path)
    if area == "backups":
        sibling = os.path.splitext(path)[0]
        if os.path.isdir(sibling) and not os.path.islink(sibling):
            shutil.rmtree(sibling)
