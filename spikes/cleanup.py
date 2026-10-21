"""Deletes every downloaded spike file (keeps nothing but results.json unless --all)."""
import os
import shutil
import sys

from common import MEDIA, RESULTS

keep_results = "--all" not in sys.argv
for name in os.listdir(MEDIA):
    path = os.path.join(MEDIA, name)
    if keep_results and path == RESULTS:
        continue
    shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
print("Spike media deleted." + (" Kept results.json." if keep_results else ""))
