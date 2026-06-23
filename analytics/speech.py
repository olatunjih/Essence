"""Speech pipeline: batch STT, TTS, audio classification."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# SPEECH PIPELINE  (batch STT, TTS, audio classification)
# ══════════════════════════════════════════════════════════════════════════════

def _tool_speech(audio_path: str, task: str = "transcribe",
                  language: str = "en",
                  hw: "HardwareProfile | None" = None) -> str:
    """
    Speech / audio tasks.
    task: transcribe | translate | classify | tts
    transcribe: faster-whisper STT (CPU-friendly)
    translate:  faster-whisper with translate task
    classify:   audio event classification via openl3 or basic energy features
    tts:        kokoro-onnx text-to-speech (returns output wav path)
    """
    tier = hw.tier if hw else 1
    p    = Path(audio_path).expanduser()

    if task in ("transcribe", "translate"):
        # faster-whisper: 200ms for 5s on Pi 4, CPU int8
        try:
            from faster_whisper import WhisperModel  # type: ignore
            model_size = ("tiny.en" if tier == 0 else
                          "base.en"  if tier == 1 else
                          "small"    if tier == 2 else "medium")
            if language != "en": model_size = model_size.replace(".en", "")
            wm = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = wm.transcribe(str(p),
                                            task="translate" if task == "translate" else "transcribe",
                                            language=language if language != "en" else None)
            text = " ".join(s.text for s in segments).strip()
            return json.dumps({"task": task, "model": model_size,
                               "language": info.language,
                               "transcript": text})
        except ImportError:
            return "[speech] Install: pip install faster-whisper"
        except Exception as e:
            return f"[speech error: {e}]"

    if task == "tts":
        text     = audio_path   # reuse param as text when task=tts
        import tempfile as _tf
        out_path = str(Path(_tf.gettempdir()) / f"essence_tts_{int(time.time())}.wav")
        try:
            import kokoro  # type: ignore
            import numpy as np
            import wave
            pipeline = kokoro.KPipeline(lang_code="en-us")
            samples: list[float] = []
            for _, _, audio in pipeline(text, voice="af_heart", speed=1.0):
                samples.extend(audio.tolist())
            arr = np.array(samples, dtype=np.float32).clip(-1, 1)
            with wave.open(out_path, "w") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(24000)
                wf.writeframes((arr * 32767).astype(np.int16).tobytes())
            return json.dumps({"task": "tts", "output": out_path,
                               "chars": len(text)})
        except ImportError:
            return "[speech/tts] Install: pip install kokoro-onnx"
        except Exception as e:
            return f"[tts error: {e}]"

    if task == "classify":
        try:
            import librosa  # type: ignore
            import numpy as np
            y_audio, sr = librosa.load(str(p), sr=22050, mono=True)
            mfcc = librosa.feature.mfcc(y=y_audio, sr=sr, n_mfcc=13)
            rms  = librosa.feature.rms(y=y_audio)
            return json.dumps({
                "task": "classify",
                "duration_s": round(float(len(y_audio) / sr), 2),
                "rms_mean": round(float(rms.mean()), 5),
                "mfcc_mean": [round(float(v), 3) for v in mfcc.mean(axis=1)],
                "note": "For event classification install: "
                        "pip install transformers and use task=transcribe"
            })
        except ImportError:
            return "[speech/classify] Install: pip install librosa"
        except Exception as e:
            return f"[audio error: {e}]"

    return f"[speech] Unknown task '{task}'. Valid: transcribe | translate | tts | classify"


# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
