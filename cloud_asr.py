"""Cloud transcription providers for audio downloaded by the local ASR pipeline."""
import base64
import glob
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Dict, List, Tuple

import llm

ROOT = os.path.dirname(os.path.abspath(__file__))
MEDIA_CACHE = os.path.join(os.environ.get("YTKB_DATA_DIR") or ROOT, "media_cache")
CHUNK_SECONDS = 600
TIMEOUT = 300
_RETRY_DELAYS = (5, 15, 45)


class CloudASRError(Exception):
    """A cloud transcription failed; messages intentionally omit request details and keys."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _post(url: str, headers: Dict[str, str], body: bytes, timeout: int) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CloudASRError(f"Transcription service returned HTTP {exc.code}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise CloudASRError("Could not reach the transcription service") from None
    except (UnicodeError, json.JSONDecodeError):
        raise CloudASRError("Transcription service returned invalid JSON") from None


def _multipart(path: str, model: str) -> Tuple[bytes, str]:
    boundary = "----YourTube" + uuid.uuid4().hex
    pieces = []
    for name, value in (("model", model), ("response_format", "verbose_json"),
                        ("timestamp_granularities[]", "segment")):
        pieces.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(),
                       value.encode(), b"\r\n"))
    filename = os.path.basename(path)
    with open(path, "rb") as audio:
        data = audio.read()
    pieces.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                   "Content-Type: audio/mpeg\r\n\r\n".encode(), data, b"\r\n",
                   f"--{boundary}--\r\n".encode()))
    return b"".join(pieces), boundary


def _openai_chunk(url: str, key: str, path: str, model: str) -> Dict[str, Any]:
    body, boundary = _multipart(path, model)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}"}
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return _post(url, headers, body, TIMEOUT)
        except CloudASRError as exc:
            if exc.status != 429 and not 500 <= exc.status <= 599:
                raise
            if attempt >= len(_RETRY_DELAYS):
                raise CloudASRError(f"Transcription service failed after retries (HTTP {exc.status})", exc.status) from None
            time.sleep(_RETRY_DELAYS[attempt])
    raise CloudASRError("Transcription service failed")


def _gemini_chunk(url: str, key: str, path: str, model: str) -> Dict[str, Any]:
    with open(path, "rb") as audio:
        encoded = base64.b64encode(audio.read()).decode("ascii")
    prompt = ('Transcribe this audio in its spoken language. Return JSON with keys "language" and '
              '"segments", where segments is an array of {"start": seconds from the start of this audio, "text": "..."}. '
              'Include one segment per spoken phrase and no other text.')
    body = json.dumps({"contents": [{"parts": [{"text": prompt},
                                                  {"inline_data": {"mime_type": "audio/mp3", "data": encoded}}]}],
                       "generationConfig": {"responseMimeType": "application/json"}}).encode()
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return _post(url, {"x-goog-api-key": key, "Content-Type": "application/json"}, body, TIMEOUT)
        except CloudASRError as exc:
            if exc.status != 429 and not 500 <= exc.status <= 599:
                raise
            if attempt >= len(_RETRY_DELAYS):
                raise CloudASRError(f"Transcription service failed after retries (HTTP {exc.status})", exc.status) from None
            time.sleep(_RETRY_DELAYS[attempt])
    raise CloudASRError("Transcription service failed")


def _gemini_result(response: Dict[str, Any]) -> Dict[str, Any]:
    try:
        text = response["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise CloudASRError("Gemini returned an invalid transcription") from None
    if not isinstance(result, dict):
        raise CloudASRError("Gemini returned an invalid transcription")
    return result


def _normalize_gemini(rows: Any, chunk_seconds: float, offset: float) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    valid = []
    previous = -1.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            start = float(row.get("start"))
        except (TypeError, ValueError):
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip() or not 0 <= start < chunk_seconds or start < previous:
            continue
        valid.append((start, text.strip()))
        previous = start
    out = []
    for index, (start, text) in enumerate(valid):
        end = valid[index + 1][0] if index + 1 < len(valid) else chunk_seconds
        out.append({"start": round(offset + start, 2), "dur": round(max(0, end - start), 2), "text": text})
    return out


def transcribe(path: str, provider: str, settings: Dict[str, Any],
               progress: Callable[[float], None] = lambda p: None) -> Tuple[List[Dict[str, Any]], str]:
    if provider not in ("groq", "openai", "gemini"):
        raise CloudASRError("Unsupported cloud transcription provider")
    cfg = (settings.get("providers") or {}).get(provider) or {}
    key = llm._api_key(provider, cfg)
    if not key:
        raise CloudASRError("Cloud transcription provider is not configured")
    from local_asr import cloud_model
    model = cloud_model(settings, provider)
    base = cfg.get("base_url") or llm.PROVIDERS[provider].get("base_url", "")
    url = (base.rstrip("/") + "/audio/transcriptions") if provider != "gemini" else (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
    os.makedirs(MEDIA_CACHE, exist_ok=True)
    prefix = os.path.join(MEDIA_CACHE, uuid.uuid4().hex + "_")
    pattern = prefix + "*.mp3"
    output_pattern = prefix + "%03d.mp3"
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "segment",
                        "-segment_time", str(CHUNK_SECONDS), "-reset_timestamps", "1", output_pattern],
                       capture_output=True, check=True)
        chunks = sorted(glob.glob(pattern))
        if not chunks:
            raise CloudASRError("Audio conversion produced no chunks")
        segments: List[Dict[str, Any]] = []
        language = ""
        for index, chunk in enumerate(chunks):
            offset = index * CHUNK_SECONDS
            if provider == "gemini":
                parsed = _gemini_result(_gemini_chunk(url, key, chunk, model))
                # a chunk of music or silence has no speech; the whole video still fails below if nothing was heard
                rows = _normalize_gemini(parsed.get("segments"), CHUNK_SECONDS, offset)
                language = language or str(parsed.get("language") or "")
                segments.extend(rows)
            else:
                data = _openai_chunk(url, key, chunk, model)
                language = language or str(data.get("language") or "")
                chunk_segments = data.get("segments")
                if not isinstance(chunk_segments, list):
                    raise CloudASRError("Transcription service returned no segments")
                for row in chunk_segments:
                    if not isinstance(row, dict):
                        continue
                    try:
                        start, end = float(row["start"]), float(row["end"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    text = row.get("text")
                    if isinstance(text, str) and text.strip() and start >= 0 and end >= start:
                        segments.append({"start": round(offset + start, 2), "dur": round(end - start, 2), "text": text.strip()})
            progress((index + 1) / len(chunks))
        if not segments:
            raise CloudASRError("Transcription service returned no valid segments")
        return segments, language
    except (subprocess.CalledProcessError, OSError):
        raise CloudASRError("Could not convert audio with ffmpeg") from None
    finally:
        for chunk in glob.glob(pattern):
            try:
                os.remove(chunk)
            except OSError:
                pass
