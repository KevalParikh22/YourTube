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
for _k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_k, None)
