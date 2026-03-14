"""VoiceClip Transcription Server — faster-whisper + FastAPI."""

from __future__ import annotations

import io
import logging
import os
import time
import wave

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voiceclip-server")

# ── Configuration via environment ────────────────────────────────────────────
API_KEY = os.environ.get("VOICECLIP_API_KEY", "")
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
CHUNK_BEAM_SIZE = int(os.environ.get("WHISPER_CHUNK_BEAM_SIZE", "1"))

# ── App & Model ──────────────────────────────────────────────────────────────
app = FastAPI(title="VoiceClip Transcription Server", version="1.0.0")

model: WhisperModel | None = None


@app.on_event("startup")
def load_model() -> None:
    global model
    logger.info("Loading model=%s device=%s compute_type=%s", MODEL_SIZE, DEVICE, COMPUTE_TYPE)
    t0 = time.monotonic()
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    logger.info("Model loaded in %.1fs", time.monotonic() - t0)


# ── Auth helper ──────────────────────────────────────────────────────────────
def _check_auth(authorization: str | None) -> None:
    if not API_KEY:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ── Endpoints ────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_SIZE, "device": DEVICE, "compute_type": COMPUTE_TYPE}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
) -> dict:
    """Transcribe a full WAV file. Used by the VoiceClip client after recording stops."""
    _check_auth(authorization)

    audio_bytes = await file.read()
    if len(audio_bytes) < 1000:
        raise HTTPException(status_code=400, detail="Audio file too small")

    t0 = time.monotonic()

    # Determine audio duration from WAV header
    duration_s = 0.0
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            duration_s = wf.getnframes() / wf.getframerate()
    except Exception:
        pass

    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=BEAM_SIZE,
        language=None,  # auto-detect
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text_parts = []
    for segment in segments:
        text_parts.append(segment.text.strip())

    text = " ".join(text_parts).strip()
    processing_ms = (time.monotonic() - t0) * 1000

    logger.info(
        "transcribe lang=%s duration=%.1fs processing=%.0fms chars=%d",
        info.language, duration_s, processing_ms, len(text),
    )

    return {
        "text": text,
        "language": info.language,
        "duration_s": round(duration_s, 1),
        "processing_ms": round(processing_ms),
    }


@app.post("/transcribe-chunk")
async def transcribe_chunk(
    file: UploadFile = File(...),
    authorization: str | None = Header(None),
) -> dict:
    """Transcribe a short audio chunk (2-5 seconds). Used for streaming mode."""
    _check_auth(authorization)

    audio_bytes = await file.read()
    if len(audio_bytes) < 500:
        return {"text": "", "processing_ms": 0}

    t0 = time.monotonic()

    segments, info = model.transcribe(
        io.BytesIO(audio_bytes),
        beam_size=CHUNK_BEAM_SIZE,
        language=None,
        vad_filter=False,  # no VAD for short chunks
    )

    text_parts = []
    for segment in segments:
        text_parts.append(segment.text.strip())

    text = " ".join(text_parts).strip()
    processing_ms = (time.monotonic() - t0) * 1000

    return {
        "text": text,
        "language": info.language,
        "processing_ms": round(processing_ms),
    }
