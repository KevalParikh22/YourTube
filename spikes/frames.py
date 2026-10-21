"""
Spike 3: pick frames worth reading. ffmpeg scene-change detection, at most one frame per
MIN_GAP seconds, then drop near-duplicates with a difference hash (dHash).
"""
import os
import re
import subprocess
import time

from PIL import Image

from common import CLIPS, MEDIA, save_result

SCENE = 0.25   # ffmpeg scene-change threshold (0..1)
MIN_GAP = 10   # seconds between kept frames
EVERY = 30     # also take a frame this often: typing and scrolling code never trip scene detection
HAMMING = 6    # dHash bits that may differ and still count as "the same slide"


def dhash(path, size=8):
    img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata())
    return sum(1 << i for i in range(size * size) if px[(i // size) * (size + 1) + i % size] > px[(i // size) * (size + 1) + i % size + 1])


def main():
    rows = []
    for vid, start, end in CLIPS:
        src = next(os.path.join(MEDIA, f) for f in os.listdir(MEDIA) if f.startswith(f"{vid}.{start}-{end}.video"))
        out = os.path.join(MEDIA, f"frames_{vid}")
        os.makedirs(out, exist_ok=True)
        for f in os.listdir(out):
            os.remove(os.path.join(out, f))
        t = time.time()
        # First frame, every scene change, and a frame at least every EVERY s; showinfo prints each frame's time
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-vf",
                               f"select='isnan(prev_selected_t)+gt(scene,{SCENE})+gte(t-prev_selected_t,{EVERY})',showinfo", "-vsync", "vfr", "-q:v", "3",
                               os.path.join(out, "raw_%04d.jpg")], capture_output=True, text=True)
        times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", proc.stderr)]
        raw = sorted(f for f in os.listdir(out) if f.startswith("raw_"))
        scene_s = time.time() - t

        kept, last_t, hashes = [], -1e9, []
        for name, ts in zip(raw, times):
            path = os.path.join(out, name)
            h = dhash(path)
            if ts - last_t < MIN_GAP or any(bin(h ^ o).count("1") <= HAMMING for o in hashes):
                os.remove(path)
                continue
            new = os.path.join(out, f"kept_{int(start + ts):05d}.jpg")
            os.rename(path, new)
            kept.append(int(start + ts))
            hashes.append(h)
            last_t = ts
        row = {"video": vid, "clip_s": [start, end], "scene_changes": len(raw), "kept": len(kept),
               "kept_per_10min": round(len(kept) / ((end - start) / 600), 1), "ffmpeg_s": round(scene_s, 1),
               "kept_times": kept}
        rows.append(row)
        print(row)
    save_result("frames", rows)


if __name__ == "__main__":
    main()
