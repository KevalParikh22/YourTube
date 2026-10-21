# Phase 2.5 spike

Measurements that decide the Phase 2.5 design (see `docs/PRD-Phase-2.5.md`, build step 1).
Media goes to `media_cache/spike/` (git-ignored) and is deleted with `python spikes/cleanup.py`.
Results are written to `media_cache/spike/results.json` and summarised in the PRD.

```bash
.venv/bin/python spikes/download.py        # anonymous, paced: smallest audio + 480p video-only clips
.venv/bin/python spikes/asr.py             # faster-whisper speed, VRAM and agreement with YouTube captions
.venv/bin/python spikes/frames.py          # scene-change frames + near-duplicate removal
.venv/bin/python spikes/ocr_vs_vision.py   # RapidOCR vs the local vision model on the kept frames
.venv/bin/python spikes/cleanup.py         # delete all downloaded media
```

Personal use only: media is kept just long enough to measure, never shared.
