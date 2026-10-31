"""
Command line for YourTube: syncs a playlist (read-only), sorts its videos into categories,
prints watch time at 1x vs 2x, and runs maintenance jobs. The dashboard (dashboard.py) is the main interface.
"""
import os
import sys
import argparse
# yt-dlp's old plugin loader registers a module named 'extractor' that hides ours (extractor.py); no plugins are used
os.environ.setdefault("YTDLP_NO_PLUGINS", "1")
import applog
from extractor import extract_playlist_metadata
from classifier import classify_playlist_videos, load_taxonomy
from storage import save_playlist, get_all_videos, init_db
from metrics import calculate_category_metrics

def run_pipeline(
    playlist_url: str,
    browser_cookies: str = None,
    cookies_file: str = None,
    ai_suggest: bool = False,
    library: int = None
):
    print("\n" + "="*60)
    print("  YourTube — playlist sync")
    print("="*60 + "\n")
    
    print(f"Step 1: Connecting to playlist: {playlist_url}")
    if browser_cookies:
        print(f"   Using local read-only session cookies from browser: {browser_cookies}")
    elif cookies_file:
        print(f"   Using local cookie file: {cookies_file}")
    else:
        print("   Ingesting as public/unlisted playlist (no cookies provided)...")
        
    try:
        data = extract_playlist_metadata(
            playlist_url=playlist_url,
            browser_cookies=browser_cookies,
            cookies_file=cookies_file
        )
    except Exception as e:
        print(f"\nError: could not read the playlist: {e}")
        print("\nTroubleshooting Tips:")
        print("   1. If your playlist is private (or Watch Later), use a signed-in browser: --browser auto")
        print("      On Linux, Chrome needs the 'secretstorage' package (run: bash scripts/setup.sh).")
        print("      On Windows, Chrome/Edge cookies are app-bound encrypted and often can't be read;")
        print("      use Firefox or export a cookies.txt and pass --cookies cookies.txt instead.")
        print("   2. Or set the YouTube playlist visibility to 'Unlisted' so it requires no cookies.")
        print("   3. \"The playlist does not exist\" usually means a wrong ID, or a private playlist read without")
        print("      a sign-in. See your playlists and their IDs with: python main.py --list --browser auto")
        cause, fix = applog.explain(str(e)) or (None, None)
        if cause and fix:
            print(f"Cause: {cause}")
            print(f"Fix: {fix}")
        if browser_cookies:
            access = f"browser:{browser_cookies}"
        elif cookies_file:
            access = "cookies"
        else:
            access = "public"
        applog.get("sync").error("Sync failed (%s): %s", access, str(e))
        return False
        
    videos = data['videos']
    if browser_cookies == 'auto' and data.get('browser'):
        print(f"   Found a YouTube sign-in in: {data['browser']}")
    if browser_cookies:
        data['access'] = f"browser:{data.get('browser') or browser_cookies}"
    elif cookies_file:
        data['access'] = "cookies"
        data['cookies_file'] = cookies_file
    else:
        data['access'] = "public"
    print(f"   Successfully extracted {len(videos)} videos (0 bytes downloaded, non-destructive).")
    if data.get('skipped_unavailable'):
        print(f"   Warning: Skipped {data['skipped_unavailable']} private/deleted/unavailable entries.")
    if not videos:
        print("\nError: No videos were extracted. Nothing was saved.")
        print("   If the playlist is private, check that the browser/cookies you chose are logged into YouTube.")
        return False

    # A playlist synced before stays in its library; a new one goes where it was added from
    from storage import playlist_library
    library = playlist_library(data['id']) or library or 1
    print("\nStep 2: Classifying videos into semantic categories...")
    classify_playlist_videos(videos, load_taxonomy(library=library))

    print("\nStep 3: Saving metadata & state to local database (yt_kb.db)...")
    counts = save_playlist(data, videos, library_id=library)
    print(f"   Saved: {counts['added']} new, {counts['updated']} refreshed, "
          f"{counts['removed']} no longer in source (kept locally, flagged as removed).")
    if ai_suggest:
        print()
        from ai_classifier import run_suggest
        run_suggest("unsorted")  # suggestions only; a failure here doesn't fail the sync
    # Reload so manual categories and existing watch statuses are reflected in the summary
    classified_videos = get_all_videos(data['id'])
    
    print("\nStep 4: Computing watch time metrics (1.0x vs 2.0x speeds)...")
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
        print(f"{cat_name:<35} | {m['total_videos']:<7} | {m['durations']['1.0x']['formatted']:<12} | {m['durations']['2.0x']['formatted']:<10}")
    print("-"*70)
    print(f"{'TOTAL SUMMARY':<35} | {overall['total_videos']:<7} | {overall['durations']['1.0x']['formatted']:<12} | {overall['durations']['2.0x']['formatted']:<10}")
    print(f"Time saved at 2.0x speed: {overall['time_saved_2x']['formatted']}")
    print("-"*70 + "\n")
    
    print("Sync finished. Open the dashboard to see it: .venv/bin/python dashboard.py")
    print("   Your source playlist was NOT modified or deleted (100% non-destructive).")
    print("   Browse, track progress and re-categorize with: python dashboard.py")
    return True

def interactive_mode():
    print("\n" + "="*60)
    print("  YourTube — Setup")
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
    parser = argparse.ArgumentParser(description="YourTube command line")
    parser.add_argument("url", nargs="?", help="YouTube playlist URL or ID")
    parser.add_argument("--browser", "-b", help="Browser to read session cookies from: auto (detect), firefox, chrome, brave, edge")
    parser.add_argument("--cookies", "-c", help="Path to cookies.txt file")
    parser.add_argument("--list", action="store_true", help="List the playlists on your signed-in account, with their IDs")
    parser.add_argument("--embed", action="store_true", help="Update the meaning-based search index (needs the Ollama embedding model)")
    parser.add_argument("--details", type=int, nargs="?", const=200, metavar="N",
                        help="Fetch descriptions, chapters and captions for up to N videos that need them (default 200)")
    parser.add_argument("--ai", action="store_true", help="After syncing, ask the configured AI for category suggestions on unsorted videos")
    
    args = parser.parse_args()

    if args.embed:
        from embedder import run_embed
        run_embed()
        return

    if args.details is not None:
        from enrich import run_enrich
        run_enrich(limit=max(1, args.details))
        return

    if args.list:
        from extractor import list_my_playlists
        try:
            playlists = list_my_playlists(args.browser or "auto", args.cookies)
        except Exception as e:
            print(f"Error: Could not list playlists: {e}")
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
            ai_suggest=args.ai
        )
    else:
        interactive_mode()

if __name__ == "__main__":
    main()
