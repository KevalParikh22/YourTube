"""
Main Command Line Interface for YouTube Knowledge Base (YT KB) - Phase 0.
Safely extracts private or unlisted playlist metadata, classifies videos into categories,
computes watch durations (1x vs 2x), and generates visual reports.
"""
import sys
import os
import argparse
import webbrowser
from extractor import extract_playlist_metadata
from classifier import classify_playlist_videos
from storage import save_playlist, get_all_videos, init_db
from reporter import generate_markdown_report, generate_html_dashboard
from metrics import calculate_category_metrics

def run_pipeline(
    playlist_url: str,
    browser_cookies: str = None,
    cookies_file: str = None,
    open_browser: bool = True,
    ai_suggest: bool = False
):
    print("\n" + "="*60)
    print("  ⚡ YouTube Knowledge Base (YT KB) — Phase 0 Pipeline")
    print("="*60 + "\n")
    
    print(f"📥 Step 1: Connecting to playlist: {playlist_url}")
    if browser_cookies:
        print(f"   🔒 Using local read-only session cookies from browser: {browser_cookies}")
    elif cookies_file:
        print(f"   🔒 Using local cookie file: {cookies_file}")
    else:
        print("   🌐 Ingesting as public/unlisted playlist (no cookies provided)...")
        
    try:
        data = extract_playlist_metadata(
            playlist_url=playlist_url,
            browser_cookies=browser_cookies,
            cookies_file=cookies_file
        )
    except Exception as e:
        print(f"\n❌ Ingestion error: {e}")
        print("\n💡 Troubleshooting Tips:")
        print("   1. If your playlist is private (or Watch Later), use a signed-in browser: --browser auto")
        print("      On Linux, Chrome needs the 'secretstorage' package (pip install -r requirements.txt).")
        print("      On Windows, Chrome/Edge cookies are app-bound encrypted and often can't be read;")
        print("      use Firefox or export a cookies.txt and pass --cookies cookies.txt instead.")
        print("   2. Or set the YouTube playlist visibility to 'Unlisted' so it requires no cookies.")
        print("   3. \"The playlist does not exist\" usually means a wrong ID, or a private playlist read without")
        print("      a sign-in. See your playlists and their IDs with: python main.py --list --browser auto")
        return False
        
    videos = data['videos']
    if browser_cookies == 'auto' and data.get('browser'):
        print(f"   🔎 Found a YouTube sign-in in: {data['browser']}")
    print(f"   ✅ Successfully extracted {len(videos)} videos (0 bytes downloaded, non-destructive).")
    if data.get('skipped_unavailable'):
        print(f"   ⚠️ Skipped {data['skipped_unavailable']} private/deleted/unavailable entries.")
    if not videos:
        print("\n❌ No videos were extracted. Nothing was saved.")
        print("   If the playlist is private, check that the browser/cookies you chose are logged into YouTube.")
        return False

    print("\n🏷️ Step 2: Classifying videos into semantic categories...")
    classify_playlist_videos(videos)

    print("\n💾 Step 3: Saving metadata & state to local database (yt_kb.db)...")
    counts = save_playlist(data, videos)
    print(f"   ✅ Saved: {counts['added']} new, {counts['updated']} refreshed, "
          f"{counts['removed']} no longer in source (kept locally, flagged as removed).")
    if ai_suggest:
        print()
        from ai_classifier import run_suggest
        run_suggest("unsorted")  # suggestions only; a failure here doesn't fail the sync
    # Reload so manual categories and existing watch statuses are reflected in the reports
    classified_videos = get_all_videos(data['id'])
    
    print("\n⏱️ Step 4: Computing watch time metrics (1.0x vs 2.0x speeds)...")
    overall = calculate_category_metrics(classified_videos)
    
    # Group videos by category
    categories = {}
    for v in classified_videos:
        categories.setdefault(v['category'], []).append(v)
        
    print("\n" + "-"*70)
    print(f"{'Category':<35} | {'Videos':<7} | {'1.0x Length':<12} | {'2.0x Length':<12}")
    print("-"*70)
    for cat_name, cat_videos in sorted(categories.items(), key=lambda x: -len(x[1])):
        m = calculate_category_metrics(cat_videos)
        print(f"{cat_name:<35} | {m['total_videos']:<7} | {m['durations']['1.0x']['formatted']:<12} | ⚡ {m['durations']['2.0x']['formatted']:<10}")
    print("-"*70)
    print(f"{'TOTAL SUMMARY':<35} | {overall['total_videos']:<7} | {overall['durations']['1.0x']['formatted']:<12} | ⚡ {overall['durations']['2.0x']['formatted']:<10}")
    print(f"🎉 Time saved at 2.0x speed: {overall['time_saved_2x']['formatted']}")
    print("-"*70 + "\n")
    
    print("📊 Step 5: Generating reports...")
    md_path = generate_markdown_report(data, classified_videos)
    html_path = generate_html_dashboard(data, classified_videos)
    print(f"   📄 Markdown Knowledge Base: {md_path}")
    print(f"   🌐 Interactive HTML Dashboard: {html_path}")
    
    if open_browser:
        print("\n🚀 Opening interactive dashboard in your default browser...")
        webbrowser.open(f"file:///{os.path.abspath(html_path)}")
        
    print("\n✅ Phase 0 Pipeline completed successfully!")
    print("   Your source playlist was NOT modified or deleted (100% non-destructive).")
    print("   Browse, track progress and re-categorize with: python dashboard.py")
    return True

def interactive_mode():
    print("\n" + "="*60)
    print("  ⚡ YouTube Knowledge Base (YT KB) — Setup")
    print("="*60 + "\n")
    
    playlist_url = input("Enter YouTube Playlist URL or ID: ").strip()
    if not playlist_url:
        print("No URL provided. Exiting.")
        return
        
    print("\nIs this playlist Private?")
    print("  1. Yes, it's Private or Watch Later (use the sign-in from my browser)")
    print("  2. No, it's Unlisted or Public (No browser cookies needed)")
    choice = input("Select option (1 or 2, default is 2): ").strip()
    
    browser = None
    if choice == "1":
        print("\nWhich browser are you signed in to YouTube with?")
        print("  Options: auto (detect), firefox, chrome, chromium, brave, edge, opera")
        browser = input("Browser (default auto): ").strip().lower() or "auto"
            
    run_pipeline(playlist_url=playlist_url, browser_cookies=browser)

def main():
    parser = argparse.ArgumentParser(description="YouTube Knowledge Base (YT KB) - Phase 0")
    parser.add_argument("url", nargs="?", help="YouTube playlist URL or ID")
    parser.add_argument("--browser", "-b", help="Browser to read session cookies from: auto (detect), firefox, chrome, brave, edge")
    parser.add_argument("--cookies", "-c", help="Path to cookies.txt file")
    parser.add_argument("--no-open", action="store_true", help="Do not open HTML report in browser automatically")
    parser.add_argument("--list", action="store_true", help="List the playlists on your signed-in account, with their IDs")
    parser.add_argument("--details", type=int, nargs="?", const=200, metavar="N",
                        help="Fetch descriptions, chapters and captions for up to N videos that need them (default 200)")
    parser.add_argument("--ai", action="store_true", help="After syncing, ask the configured AI for category suggestions on unsorted videos")
    
    args = parser.parse_args()

    if args.details is not None:
        from enrich import run_enrich
        run_enrich(limit=max(1, args.details))
        return

    if args.list:
        from extractor import list_my_playlists
        try:
            playlists = list_my_playlists(args.browser or "auto", args.cookies)
        except Exception as e:
            print(f"❌ Could not list playlists: {e}")
            return
        width = max((len(p['id']) for p in playlists), default=2)
        for p in playlists:
            print(f"{p['id']:<{width}}  {p['title']}")
        print(f"\n{len(playlists)} playlists. Sync one with: python main.py <ID> --browser auto")
        return

    if args.url:
        run_pipeline(
            playlist_url=args.url,
            browser_cookies=args.browser,
            cookies_file=args.cookies,
            open_browser=not args.no_open,
            ai_suggest=args.ai
        )
    else:
        interactive_mode()

if __name__ == "__main__":
    main()
