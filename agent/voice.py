"""
voice.py — fully local speech I/O for the voice channel.

STT: faster-whisper (CTranslate2 backend, multilingual).
TTS: Piper (ONNX, single French voice).

Both models are lazy-initialized with the same thread-safe pattern used by
tools/rag_tools.py's _get_collection() — a module-level global guarded by a
lock, reset to None on failure so the next call retries instead of wedging
the process in a permanently-broken state.

This module is a pure audio<->text utility: it knows nothing about sessions
or the LangGraph pipeline (see main.py's /transcribe and /synthesize).
"""

import io
import os
import threading
import wave
from pathlib import Path

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "fr")
WHISPER_DOWNLOAD_ROOT = os.getenv("WHISPER_DOWNLOAD_ROOT", "/root/.cache/whisper")

PIPER_VOICE = os.getenv("PIPER_VOICE", "fr_FR-siwis-medium")
PIPER_VOICES_DIR = os.getenv("PIPER_VOICES_DIR", "/root/.cache/piper")


class VoiceUnavailableError(RuntimeError):
    """Raised when the STT/TTS model can't be loaded or fails to run."""


class VoiceInputError(ValueError):
    """Raised when the caller-supplied audio/text input is malformed/empty."""


# ─────────────────────────────────────────────
# STT — faster-whisper, lazy init
# ─────────────────────────────────────────────

_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            os.makedirs(WHISPER_DOWNLOAD_ROOT, exist_ok=True)
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                download_root=WHISPER_DOWNLOAD_ROOT,
            )
    return _whisper_model


def transcribe(audio_bytes: bytes) -> str:
    """
    Transcribe raw audio bytes (any container ffmpeg/PyAV can decode —
    webm/opus, wav, mp3, ...) to text. Returns "" if the model produced no
    speech. Raises VoiceInputError for empty/unparseable input, or
    VoiceUnavailableError if the model can't be loaded/run.
    """
    if not audio_bytes:
        raise VoiceInputError("empty audio payload")

    try:
        model = _get_whisper_model()
    except Exception as e:
        raise VoiceUnavailableError(f"whisper model unavailable: {e}") from e

    try:
        segments, _info = model.transcribe(
            io.BytesIO(audio_bytes),
            language=WHISPER_LANGUAGE or None,
            beam_size=5,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as e:
        global _whisper_model
        # Only reset on load-shaped failures; a bad single clip shouldn't
        # force reloading a perfectly good model for the next request. We
        # can't easily distinguish here, so be conservative: leave the model
        # in place and surface a clear input error instead.
        raise VoiceInputError(f"could not transcribe audio: {e}") from e


# ─────────────────────────────────────────────
# TTS — Piper, lazy init
# ─────────────────────────────────────────────

_piper_voice = None
_piper_lock = threading.Lock()


def _get_piper_voice():
    global _piper_voice
    with _piper_lock:
        if _piper_voice is None:
            from piper import PiperVoice
            from piper.download_voices import download_voice

            os.makedirs(PIPER_VOICES_DIR, exist_ok=True)
            model_path = os.path.join(PIPER_VOICES_DIR, f"{PIPER_VOICE}.onnx")
            if not os.path.exists(model_path):
                download_voice(PIPER_VOICE, download_dir=Path(PIPER_VOICES_DIR))
            _piper_voice = PiperVoice.load(model_path)
    return _piper_voice


def synthesize(text: str) -> bytes:
    """
    Synthesize `text` to WAV audio bytes. Raises VoiceInputError for
    empty text, or VoiceUnavailableError if the voice can't be
    loaded/run (e.g. first-run download failure).
    """
    if not text or not text.strip():
        raise VoiceInputError("empty text")

    try:
        voice = _get_piper_voice()
    except Exception as e:
        global _piper_voice
        with _piper_lock:
            _piper_voice = None
        raise VoiceUnavailableError(f"piper voice unavailable: {e}") from e

    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file)
        return buf.getvalue()
    except Exception as e:
        raise VoiceUnavailableError(f"could not synthesize audio: {e}") from e
