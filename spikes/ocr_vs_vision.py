"""
Spike 4: read the kept frames two ways and compare.
- RapidOCR (ONNX, CPU): exact on-screen text.
- The local vision model through Ollama (qwen3.5:4b): on-screen text plus a description of diagrams.
Writes media_cache/spike/ocr_vs_vision.md with both outputs per frame for a side-by-side look.
"""
import base64
import json
import os
import sys
import time
import urllib.request

from common import GpuPeak, MEDIA, save_result

PER_VIDEO = 6
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:4b"
PROMPT = ("This is a frame from a YouTube tutorial. 1) Copy the main on-screen text (slide text, code, terminal), "
          "keeping line breaks, at most 60 lines. 2) If there is a diagram, chart or whiteboard drawing, describe it in "
          "one sentence; otherwise say \"none\". Reply as JSON: {\"text\": \"...\", \"visual\": \"...\"}")


def vision(path):
    with open(path, "rb") as f:
        img = base64.b64encode(f.read()).decode()
    body = {"model": MODEL, "stream": False, "format": "json", "think": False, "keep_alive": "5m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "user", "content": PROMPT, "images": [img]}]}
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)["message"]["content"]


def main():
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    frames = []
    for d in sorted(x for x in os.listdir(MEDIA) if x.startswith("frames_")):
        kept = sorted(f for f in os.listdir(os.path.join(MEDIA, d)) if f.startswith("kept_"))
        step = max(1, len(kept) // PER_VIDEO)
        frames += [os.path.join(MEDIA, d, f) for f in kept[::step][:PER_VIDEO]]

    rows, report = [], ["# OCR vs vision model", ""]
    for path in frames:
        t = time.time()
        result, _ = ocr(path)
        ocr_s = time.time() - t
        ocr_text = "\n".join(r[1] for r in result or [])
        with GpuPeak() as peak:
            t = time.time()
            try:
                v = vision(path)
            except Exception as e:  # keep going: one bad frame shouldn't end the spike
                v = json.dumps({"error": str(e)[:200]})
            vis_s = time.time() - t
        row = {"frame": os.path.relpath(path, MEDIA), "ocr_s": round(ocr_s, 2), "ocr_chars": len(ocr_text),
               "vision_s": round(vis_s, 1), "vision_chars": len(v), "gpu_peak_mb": peak.peak}
        rows.append(row)
        print(row)
        report += [f"## {row['frame']}", "", f"![]({row['frame']})", "", f"**RapidOCR** ({row['ocr_s']} s)", "",
                   "```", ocr_text[:1500], "```", "", f"**{MODEL}** ({row['vision_s']} s)", "", "```", v[:1500], "```", ""]
    with open(os.path.join(MEDIA, "ocr_vs_vision.md"), "w") as f:
        f.write("\n".join(report))
    save_result("ocr_vs_vision", {"model": MODEL, "frames": rows})


if __name__ == "__main__":
    main()
