"""Short, local voice recordings for the About you form."""
import os
import threading
import uuid

import local_asr

VOICE_DIR = os.path.join(local_asr.ROOT, "media_cache", "voice")
MAX_BYTES = 8_000_000
MAX_SECONDS = 180
MODEL = ("small", "int8")

_model_lock = threading.Lock()
_transcribe_lock = threading.Lock()
_whisper = None


def available() -> bool:
    return local_asr.available()


def _model():
    global _whisper
    with _model_lock:
        if _whisper is None:
            _whisper = local_asr.Transcriber(MODEL[0], "cpu", MODEL[1])
        return _whisper


def _extension(mime: str) -> str:
    if isinstance(mime, str):
        mime = mime.lower().split(";", 1)[0].strip()
        if mime == "audio/webm":
            return "webm"
        if mime == "audio/ogg":
            return "ogg"
        if mime == "audio/mp4":
            return "m4a"
    raise ValueError("Unsupported audio format")


def transcribe_clip(data: bytes, mime: str, transcriber=None) -> dict:
    """Decode and transcribe one short clip, deleting its temporary file in all cases."""
    if not data:
        raise ValueError("No audio received")
    if len(data) > MAX_BYTES:
        raise ValueError("Recording is too long")
    ext = _extension(mime)
    os.makedirs(VOICE_DIR, exist_ok=True)
    path = os.path.join(VOICE_DIR, f"{uuid.uuid4()}.{ext}")
    try:
        with open(path, "wb") as audio_file:
            audio_file.write(data)
        try:
            samples = local_asr.decode(path)
        except Exception:
            raise ValueError("Could not read the recording") from None
        if len(samples) > MAX_SECONDS * 16000:
            raise ValueError("Recording is too long (3 minutes at most)")
        with _transcribe_lock:
            segments, language = (transcriber or _model())(samples)
        text = " ".join(segment.get("text", "").strip() for segment in segments if segment.get("text", "").strip())
        if not text:
            raise ValueError("No speech was heard. Try again a little closer to the microphone")
        return {"text": text, "language": language, "seconds": round(len(samples) / 16000, 2)}
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
