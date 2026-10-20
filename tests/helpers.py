import os
import tempfile


def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return path


def video(vid, title="Some video", channel="Some channel", duration=600, position=0, category="Uncategorized"):
    return {"video_id": vid, "title": title, "channel": channel, "duration_seconds": duration,
            "url": f"https://www.youtube.com/watch?v={vid}", "position": position,
            "category": category, "status": "unwatched"}


def playlist(pid="PL1", title="Test list"):
    return {"id": pid, "title": title, "url": f"https://www.youtube.com/playlist?list={pid}"}
