"""Voice pipeline: Whisper STT + Kokoro/Piper TTS."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# VOICE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
# Adds voice I/O to Essence. Works offline on T1+ hardware.
#
# STT: faster-whisper (quantised Whisper, CPU-fast on T1, GPU-fast on T2/T3)
# TTS: kokoro-onnx (high-quality neural TTS, no API key needed)
#      Falls back to pyttsx3 (system TTS) when kokoro not available.
#
# Usage:
#   voice = VoicePipeline()
#   text  = voice.transcribe("recording.wav")   # STT
#   voice.speak("Hello, I am Essence.")             # TTS
#
# In the FastAPI server, POST /api/voice/transcribe accepts audio bytes.
# The TUI shows a microphone button when voice is available.
#
# Env vars:
#   Essence_VOICE_MODEL=tiny|base|small|medium|large   Whisper model size
#   Essence_VOICE_LANG=en                              Transcription language
#   Essence_TTS_VOICE=af_heart                         Kokoro voice name
#   Essence_VOICE_DEVICE=cpu|cuda                      STT inference device

class VoicePipeline:
    """
    Offline voice I/O pipeline.

    STT: faster-whisper (quantised GGML Whisper).
    TTS: kokoro-onnx → pyttsx3 fallback → silent fallback.
    """

    def __init__(self) -> None:
        self._whisper_model_size = os.environ.get("Essence_VOICE_MODEL", "base")
        self._lang     = os.environ.get("Essence_VOICE_LANG",  "en")
        self._tts_voice = os.environ.get("Essence_TTS_VOICE", "af_heart")
        self._device   = os.environ.get("Essence_VOICE_DEVICE", "cpu")
        self._stt: Any = None   # lazy-loaded faster-whisper model
        self._tts: Any = None   # lazy-loaded kokoro-onnx pipeline

    # ── STT ─────────────────────────────────────────────────────────────────
    def _load_stt(self) -> bool:
        """Lazy-load faster-whisper. Returns True if available."""
        if self._stt is not None:
            return True
        try:
            from faster_whisper import WhisperModel  # type: ignore
            self._stt = WhisperModel(
                self._whisper_model_size,
                device=self._device,
                compute_type="int8" if self._device == "cpu" else "float16",
            )
            return True
        except ImportError:
            log.debug("whisper_not_installed",
                      extra={"detail": "pip install faster-whisper"})
            return False
        except Exception as e:
            log.warning("whisper_load_error", extra={"error": str(e)[:80]})
            return False

    def transcribe(self, audio_path: str | Path, language: str = "") -> str:
        """
        Transcribe audio file → text using faster-whisper.
        Accepts .wav, .mp3, .ogg, .flac.
        Returns empty string if STT unavailable.
        """
        if not self._load_stt():
            return ""
        lang = language or self._lang
        try:
            segments, _ = self._stt.transcribe(
                str(audio_path), language=lang,
                beam_size=5, best_of=5,
                vad_filter=True,         # skip silence automatically
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:
            return f"[transcribe error: {e}]"

    def transcribe_bytes(self, audio_bytes: bytes,
                         suffix: str = ".wav", language: str = "") -> str:
        """Transcribe raw audio bytes (e.g. from HTTP upload)."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp = f.name
        try:
            return self.transcribe(tmp, language)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    # ── TTS ─────────────────────────────────────────────────────────────────
    def _load_tts(self) -> bool:
        """
        Lazy-load kokoro-onnx TTS. Returns True if available.

        Model file search order:
          1. workspace/models/kokoro-v0_19.onnx   (preferred — workspace-managed)
          2. ~/.essence/models/kokoro-v0_19.onnx      (default workspace)
          3. ./kokoro-v0_19.onnx                   (cwd — legacy fallback)

        Download: https://huggingface.co/hexgrad/Kokoro-82M
        """
        if self._tts is not None:
            return True
        try:
            from kokoro_onnx import Kokoro  # type: ignore
            # Search for model file in workspace then cwd
            _model_name  = "kokoro-v0_19.onnx"
            _voices_name = "voices.bin"
            _search_dirs = [
                Path(os.environ.get("Essence_WORKSPACE",
                                    str(Path.home() / ".essence"))) / "models",
                Path.cwd(),
            ]
            _model_path  = next(
                (d / _model_name  for d in _search_dirs if (d / _model_name).exists()),
                Path(_model_name))   # fallback — Kokoro raises if not found
            _voices_path = next(
                (d / _voices_name for d in _search_dirs if (d / _voices_name).exists()),
                Path(_voices_name))
            self._tts = Kokoro(str(_model_path), str(_voices_path))
            return True
        except ImportError:
            return False
        except Exception as e:
            log.debug("tts_load_error", extra={"error": str(e)[:80]})
            return False

    def speak(self, text: str, output_path: str | Path | None = None) -> bool:
        """
        Synthesise speech from text.
        If output_path is given, writes WAV there.
        Otherwise plays through default audio device (sounddevice / playsound).
        Returns True on success.
        """
        if not text.strip():
            return True

        # Try kokoro-onnx (neural TTS, high quality)
        if self._load_tts():
            try:
                import numpy as np       # type: ignore
                samples, sample_rate = self._tts.create(
                    text, voice=self._tts_voice, speed=1.0, lang=self._lang)
                if output_path:
                    import soundfile as sf  # type: ignore
                    sf.write(str(output_path), samples, sample_rate)
                else:
                    import sounddevice as sd  # type: ignore
                    sd.play(samples, sample_rate)
                    sd.wait()
                return True
            except Exception as e:
                log.debug("tts_kokoro_error", extra={"error": str(e)[:80]})

        # Fallback: pyttsx3 (system TTS — no quality guarantee)
        try:
            import pyttsx3  # type: ignore
            engine = pyttsx3.init()
            if output_path:
                engine.save_to_file(text, str(output_path))
                engine.runAndWait()
            else:
                engine.say(text)
                engine.runAndWait()
            return True
        except Exception:
            pass

        # Silent fallback — log but don't crash
        log.debug("tts_unavailable",
                  extra={"detail": "install faster-whisper kokoro-onnx sounddevice"})
        return False

    @property
    def available(self) -> bool:
        """True if at least STT is available (Whisper can be loaded)."""
        return self._load_stt()


# Module-level singleton (lazy) — lock prevents race on concurrent first calls
_voice_pipeline: VoicePipeline | None = None
_voice_pipeline_lock = threading.Lock()

def get_voice_pipeline() -> VoicePipeline:
    """
    Return the module-level VoicePipeline singleton.
    Thread-safe: double-checked locking ensures Whisper loads exactly once
    even under concurrent FastAPI requests. Without the lock, two simultaneous
    requests on a fresh server both read None and try to load the model into
    GPU memory simultaneously, causing OOM on T1 hardware.
    """
    global _voice_pipeline
    if _voice_pipeline is not None:
        return _voice_pipeline
    with _voice_pipeline_lock:
        if _voice_pipeline is None:   # double-checked locking
            _voice_pipeline = VoicePipeline()
    return _voice_pipeline


# ══════════════════════════════════════════════════════════════════════════════
