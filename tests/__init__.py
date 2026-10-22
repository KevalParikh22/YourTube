"""
Test package. Points every module at throwaway files before anything imports them,
so tests never touch the real yt_kb.db, taxonomy.json or settings.json.
Run from the project root: python -m unittest
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="ytkb-tests-")
os.environ["YTKB_DB"] = os.path.join(_TMP, "default.db")
os.environ["YTKB_TAXONOMY"] = os.path.join(_TMP, "taxonomy.json")
os.environ["YTKB_SETTINGS"] = os.path.join(_TMP, "settings.json")
# No test may use a real API key, except tests/test_live.py when YTKB_LIVE=1 (kept here, in memory only)
LIVE_KEYS = {}
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    _v = os.environ.pop(_k, None)
    if _v and os.environ.get("YTKB_LIVE") == "1":
        LIVE_KEYS[_k] = _v
