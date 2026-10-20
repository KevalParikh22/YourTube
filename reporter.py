"""
Report generator for YT KB.
Generates an interactive visual HTML dashboard and a comprehensive Markdown knowledge base file.
"""
import os
from html import escape
from typing import List, Dict, Any
from metrics import calculate_category_metrics, format_seconds, format_hms
from exporter import generate_youtube_watch_queue_url

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

def ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)

def generate_markdown_report(
    playlist_info: Dict[str, Any],
    videos: List[Dict[str, Any]],
    output_filename: str = "playlist_summary.md"
) -> str:
    """Generates a structured Markdown knowledge base report."""
    ensure_reports_dir()
    filepath = os.path.join(REPORTS_DIR, output_filename)
    
    # Group videos by category
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for v in videos:
        cat = v.get("category", "General / Other")
        categories.setdefault(cat, []).append(v)
        
    overall_metrics = calculate_category_metrics(videos)
    
    lines = [
        f"# 📚 YouTube Knowledge Base: {playlist_info.get('title', 'Playlist Overview')}",
        f"**Source URL**: {playlist_info.get('url', 'N/A')}  ",
        f"**Total Videos**: {overall_metrics['total_videos']}  ",
        f"**Total Duration (1.0x)**: {overall_metrics['durations']['1.0x']['formatted']}  ",
        f"**Total Duration at 2.0x Speed**: **{overall_metrics['durations']['2.0x']['formatted']}** (⚡ Saves {overall_metrics['time_saved_2x']['formatted']})  ",
        f"**Progress**: {overall_metrics['completed_videos']}/{overall_metrics['total_videos']} completed ({overall_metrics['completion_percentage']}%)",
        "",
        "---",
        "",
        "## ⏱️ Category Time-to-Complete Matrix",
        "",
        "| Category | Videos | Duration (1.0x) | Duration (1.5x) | Duration (2.0x) | Time Saved (2x) | Instant Playlist |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |"
    ]
    
    for cat_name, cat_videos in sorted(categories.items(), key=lambda x: -len(x[1])):
        m = calculate_category_metrics(cat_videos)
        queue_url = generate_youtube_watch_queue_url([v['video_id'] for v in cat_videos])
        play_link = f"[▶️ Play Queue]({queue_url})" if queue_url else "N/A"
        lines.append(
            f"| **{cat_name}** | {m['total_videos']} | "
            f"{m['durations']['1.0x']['formatted']} | "
            f"{m['durations']['1.5x']['formatted']} | "
            f"**{m['durations']['2.0x']['formatted']}** | "
            f"{m['time_saved_2x']['formatted']} | "
            f"{play_link} |"
        )
        
    lines.extend(["", "---", "", "## 📂 Detailed Categorized Video Library", ""])
    
    for cat_name, cat_videos in sorted(categories.items(), key=lambda x: -len(x[1])):
        m = calculate_category_metrics(cat_videos)
        queue_url = generate_youtube_watch_queue_url([v['video_id'] for v in cat_videos])
        queue_btn = f" — [▶️ Launch {cat_name} Playlist on YouTube]({queue_url})" if queue_url else ""
        lines.append(f"### 🏷️ {cat_name} ({m['total_videos']} videos • {m['durations']['2.0x']['formatted']} @ 2x){queue_btn}")
        lines.append("")
        for idx, v in enumerate(cat_videos, 1):
            dur_fmt = format_seconds(v.get('duration_seconds', 0))
            dur_2x = format_seconds(v.get('duration_seconds', 0) / 2.0)
            status_icon = "✅" if v.get('status') == 'completed' else "⏳"
            lines.append(f"{idx}. {status_icon} [{v['title']}]({v['url']}) — `{dur_fmt}` *(2x: `{dur_2x}`)* | *{v.get('channel', 'Channel')}*")
        lines.append("")
        
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    return filepath

def generate_html_dashboard(
    playlist_info: Dict[str, Any],
    videos: List[Dict[str, Any]],
    output_filename: str = "playlist_dashboard.html"
) -> str:
    """Generates an interactive modern HTML dashboard."""
    ensure_reports_dir()
    filepath = os.path.join(REPORTS_DIR, output_filename)

    # Titles and channels are user content; escape before interpolating into HTML
    playlist_info = {**playlist_info, 'title': escape(playlist_info.get('title') or 'YouTube Knowledge Base')}
    videos = [
        {**v, 'title': escape(v.get('title') or ''), 'channel': escape(v.get('channel') or 'Channel'),
         'url': escape(v.get('url') or ''), 'category': escape(v.get('category') or 'General / Other')}
        for v in videos
    ]

    categories: Dict[str, List[Dict[str, Any]]] = {}
    for v in videos:
        cat = v.get("category", "General / Other")
        categories.setdefault(cat, []).append(v)
        
    overall = calculate_category_metrics(videos)
    
    # Build category cards HTML
    category_cards_html = []
    category_sections_html = []
    
    for cat_name, cat_videos in sorted(categories.items(), key=lambda x: -len(x[1])):
        m = calculate_category_metrics(cat_videos)
        queue_url = generate_youtube_watch_queue_url([v['video_id'] for v in cat_videos])
        
        # Category Card
        category_cards_html.append(f"""
        <div class="card stat-card">
            <div class="card-header">
                <h3>{cat_name}</h3>
                <span class="badge">{m['total_videos']} videos</span>
            </div>
            <div class="speed-row">
                <div class="speed-stat">
                    <span class="label">Standard (1.0x)</span>
                    <span class="val">{m['durations']['1.0x']['formatted']}</span>
                </div>
                <div class="speed-stat highlight">
                    <span class="label">⚡ 2.0x Speed</span>
                    <span class="val">{m['durations']['2.0x']['formatted']}</span>
                </div>
            </div>
            <div class="saved-banner">Saves {m['time_saved_2x']['formatted']} at 2x</div>
            <a href="{queue_url}" target="_blank" class="play-btn">▶️ Play Queue on YouTube</a>
        </div>
        """)
        
        # Category Video Table Section
        rows = []
        for idx, v in enumerate(cat_videos, 1):
            dur_1x = format_seconds(v.get('duration_seconds', 0))
            dur_2x = format_seconds(v.get('duration_seconds', 0) / 2.0)
            rows.append(f"""
            <tr>
                <td>{idx}</td>
                <td>
                    <a href="{v['url']}" target="_blank" class="video-title">{v['title']}</a>
                    <div class="channel-name">{v.get('channel', 'Channel')}</div>
                </td>
                <td><span class="dur-badge">{dur_1x}</span></td>
                <td><span class="dur-badge speed-badge">{dur_2x}</span></td>
            </tr>
            """)
            
        category_sections_html.append(f"""
        <div class="section-block">
            <div class="section-title">
                <div>
                    <h2>{cat_name}</h2>
                    <span class="section-meta">{m['total_videos']} videos • {m['durations']['1.0x']['formatted']} (1x) • {m['durations']['2.0x']['formatted']} (2x)</span>
                </div>
                <a href="{queue_url}" target="_blank" class="btn-queue">▶️ Play on YouTube</a>
            </div>
            <table class="video-table">
                <thead>
                    <tr>
                        <th width="50">#</th>
                        <th>Title & Channel</th>
                        <th width="110">1.0x Length</th>
                        <th width="110">2.0x Length</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        </div>
        """)
        
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>YT KB — {playlist_info.get('title', 'Playlist Overview')}</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #38bdf8;
            --accent-green: #4ade80;
            --accent-orange: #fb923c;
            --border: #334155;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 32px 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        header {{ margin-bottom: 32px; border-bottom: 1px solid var(--border); padding-bottom: 24px; }}
        .header-title {{ font-size: 2rem; font-weight: 700; margin-bottom: 8px; color: #fff; }}
        .header-subtitle {{ color: var(--text-secondary); font-size: 0.95rem; }}
        
        .hero-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 36px;
        }}
        .hero-card {{
            background: var(--surface);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border);
        }}
        .hero-card.primary {{ border-color: var(--accent); }}
        .hero-card.speed {{ border-color: var(--accent-green); }}
        .hero-label {{ font-size: 0.85rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
        .hero-val {{ font-size: 1.85rem; font-weight: 700; color: #fff; }}
        .hero-sub {{ font-size: 0.85rem; color: var(--accent-green); margin-top: 4px; }}

        .grid-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 40px;
        }}
        .stat-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
        }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }}
        .card-header h3 {{ font-size: 1.05rem; font-weight: 600; color: #fff; }}
        .badge {{ background: #0284c7; color: #fff; font-size: 0.75rem; font-weight: 600; padding: 2px 8px; border-radius: 12px; }}
        .speed-row {{ display: flex; justify-content: space-between; margin-bottom: 12px; }}
        .speed-stat .label {{ display: block; font-size: 0.75rem; color: var(--text-secondary); }}
        .speed-stat .val {{ font-size: 1.1rem; font-weight: 600; color: #f1f5f9; }}
        .speed-stat.highlight .val {{ color: var(--accent-green); }}
        .saved-banner {{ background: rgba(74, 222, 128, 0.1); color: var(--accent-green); font-size: 0.8rem; padding: 6px 10px; border-radius: 6px; text-align: center; font-weight: 500; }}

        .section-block {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 28px; overflow: hidden; }}
        .section-title {{ padding: 18px 24px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); }}
        .section-title h2 {{ font-size: 1.25rem; font-weight: 600; }}
        .section-meta {{ font-size: 0.85rem; color: var(--text-secondary); }}
        
        .video-table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        .video-table th {{ background: rgba(0,0,0,0.2); padding: 12px 20px; font-size: 0.8rem; text-transform: uppercase; color: var(--text-secondary); }}
        .video-table td {{ padding: 12px 20px; border-top: 1px solid var(--border); font-size: 0.9rem; }}
        .video-table tr:hover {{ background: var(--surface-hover); }}
        .video-title {{ color: #e2e8f0; text-decoration: none; font-weight: 500; }}
        .video-title:hover {{ color: var(--accent); text-decoration: underline; }}
        .channel-name {{ font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px; }}
        .dur-badge {{ font-family: monospace; background: #334155; padding: 3px 8px; border-radius: 4px; font-size: 0.85rem; }}
        .speed-badge {{ background: rgba(74, 222, 128, 0.15); color: var(--accent-green); font-weight: 600; }}
        .play-btn {{
            display: block;
            margin-top: 12px;
            text-align: center;
            background: #2563eb;
            color: #fff;
            text-decoration: none;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            transition: background 0.2s;
        }}
        .play-btn:hover {{ background: #1d4ed8; }}
        .btn-queue {{
            display: inline-block;
            background: #2563eb;
            color: #fff;
            text-decoration: none;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
        }}
        .btn-queue:hover {{ background: #1d4ed8; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1 class="header-title">⚡ YT KB: {playlist_info.get('title', 'YouTube Knowledge Base')}</h1>
            <p class="header-subtitle">Non-destructive intelligent categorization & 2x watch duration metrics</p>
        </header>

        <section class="hero-stats">
            <div class="hero-card primary">
                <div class="hero-label">Total Videos</div>
                <div class="hero-val">{overall['total_videos']}</div>
                <div class="hero-sub">{len(categories)} Categories</div>
            </div>
            <div class="hero-card">
                <div class="hero-label">Standard Duration (1.0x)</div>
                <div class="hero-val">{overall['durations']['1.0x']['formatted']}</div>
                <div class="hero-sub">Normal playback</div>
            </div>
            <div class="hero-card speed">
                <div class="hero-label">⚡ Watch Time @ 2.0x</div>
                <div class="hero-val">{overall['durations']['2.0x']['formatted']}</div>
                <div class="hero-sub">⚡ Saves {overall['time_saved_2x']['formatted']}</div>
            </div>
            <div class="hero-card">
                <div class="hero-label">Watch Time @ 1.5x</div>
                <div class="hero-val">{overall['durations']['1.5x']['formatted']}</div>
                <div class="hero-sub">Mid-speed playback</div>
            </div>
        </section>

        <section class="grid-cards">
            {''.join(category_cards_html)}
        </section>

        <section class="video-sections">
            {''.join(category_sections_html)}
        </section>
    </div>
</body>
</html>
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
        
    return filepath
