"""
Export and playlist creation utilities.
Provides:
1. YouTube Multi-Video Queue URLs (watch_videos endpoint)
2. JSON export per category and CSV export
"""
import os
import io
import csv
import json
from typing import List, Dict, Any

def generate_youtube_watch_queue_url(video_ids: List[str]) -> str:
    """
    Generates an instant YouTube anonymous playlist URL.
    Opening this URL creates a queue player in YouTube containing all specified videos.
    """
    if not video_ids:
        return ""
    joined_ids = ",".join(video_ids[:50])  # YouTube supports up to 50 IDs in this endpoint
    return f"https://www.youtube.com/watch_videos?video_ids={joined_ids}"

def generate_youtube_watch_queue_urls(video_ids: List[str], chunk_size: int = 50) -> List[str]:
    """Splits a long list into several 50-video queue URLs so nothing is silently dropped."""
    return [
        generate_youtube_watch_queue_url(video_ids[i:i + chunk_size])
        for i in range(0, len(video_ids), chunk_size)
    ]

EXPORT_FIELDS = ["video_id", "title", "channel", "category", "status", "duration_seconds", "url", "notes"]

def videos_to_csv(videos: List[Dict[str, Any]]) -> str:
    """Serialises videos to CSV text (UTF-8, header row included)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for v in videos:
        writer.writerow({k: v.get(k, "") for k in EXPORT_FIELDS})
    return buf.getvalue()

def export_categories_to_json(
    categories: Dict[str, List[Dict[str, Any]]],
    output_dir: str
) -> Dict[str, str]:
    """Exports each category to a distinct JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    exported_files = {}
    
    for cat_name, videos in categories.items():
        safe_name = "".join(c for c in cat_name if c.isalnum() or c in (' ', '_', '-')).strip()
        filename = f"{safe_name}.json"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({
                "category": cat_name,
                "video_count": len(videos),
                "videos": videos
            }, f, indent=2)
            
        exported_files[cat_name] = filepath
        
    return exported_files
