"""
Metrics and duration computation engine.
Calculates watch times at standard (1.0x) and accelerated speeds (1.25x, 1.5x, 1.75x, 2.0x),
video counts, time saved, and category-level completion percentages.
"""
from typing import List, Dict, Any

def format_seconds(seconds: float) -> str:
    """Converts seconds into a human-readable string like '2h 15m' or '45m 10s'."""
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    if hours == 0:
        parts.append(f"{secs}s")
        
    return " ".join(parts) if parts else "0s"

def format_hms(seconds: float) -> str:
    """Converts seconds into HH:MM:SS format."""
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def calculate_speed_duration(seconds: float, speed: float = 1.0) -> float:
    """Calculates duration when played at a specific speed multiplier."""
    if speed <= 0:
        raise ValueError("Speed multiplier must be positive.")
    return seconds / speed

def calculate_category_metrics(videos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes comprehensive metrics for a given list of videos in a category or entire playlist:
    - total_videos
    - completed_videos, unwatched_videos
    - total_seconds_1x, total_seconds_1_25x, total_seconds_1_5x, total_seconds_1_75x, total_seconds_2x
    - remaining_seconds_1x, remaining_seconds_2x
    - time_saved_at_2x
    - completion_percentage
    """
    total_videos = len(videos)
    completed = [v for v in videos if v.get("status") == "completed"]
    completed_videos = len(completed)
    unwatched_videos = total_videos - completed_videos
    
    total_sec = sum(v.get("duration_seconds", 0) for v in videos)
    remaining_sec = sum(v.get("duration_seconds", 0) for v in videos if v.get("status") != "completed")
    
    sec_1x = total_sec
    sec_1_25x = calculate_speed_duration(total_sec, 1.25)
    sec_1_5x = calculate_speed_duration(total_sec, 1.5)
    sec_1_75x = calculate_speed_duration(total_sec, 1.75)
    sec_2x = calculate_speed_duration(total_sec, 2.0)
    
    rem_1x = remaining_sec
    rem_2x = calculate_speed_duration(remaining_sec, 2.0)
    
    time_saved_at_2x = sec_1x - sec_2x
    completion_pct = (completed_videos / total_videos * 100.0) if total_videos > 0 else 0.0
    
    return {
        "total_videos": total_videos,
        "completed_videos": completed_videos,
        "unwatched_videos": unwatched_videos,
        "completion_percentage": round(completion_pct, 1),
        "durations": {
            "1.0x": {
                "seconds": sec_1x,
                "formatted": format_seconds(sec_1x),
                "hms": format_hms(sec_1x)
            },
            "1.25x": {
                "seconds": sec_1_25x,
                "formatted": format_seconds(sec_1_25x),
                "hms": format_hms(sec_1_25x)
            },
            "1.5x": {
                "seconds": sec_1_5x,
                "formatted": format_seconds(sec_1_5x),
                "hms": format_hms(sec_1_5x)
            },
            "1.75x": {
                "seconds": sec_1_75x,
                "formatted": format_seconds(sec_1_75x),
                "hms": format_hms(sec_1_75x)
            },
            "2.0x": {
                "seconds": sec_2x,
                "formatted": format_seconds(sec_2x),
                "hms": format_hms(sec_2x)
            }
        },
        "remaining": {
            "1.0x": {
                "seconds": rem_1x,
                "formatted": format_seconds(rem_1x),
                "hms": format_hms(rem_1x)
            },
            "2.0x": {
                "seconds": rem_2x,
                "formatted": format_seconds(rem_2x),
                "hms": format_hms(rem_2x)
            }
        },
        "time_saved_2x": {
            "seconds": time_saved_at_2x,
            "formatted": format_seconds(time_saved_at_2x)
        }
    }
