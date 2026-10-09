from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from .config import settings
from .media import MediaError, MediaInfo, extract_audio, fmt_time, read_wav

log = logging.getLogger("clipsense")

Progress = Callable[[float, str], None]

API_PROVIDERS = {
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "whisper-large-v3-turbo"),
    "openai": ("OPENAI_API_KEY", None, "whisper-1"),
}
API_CHUNK_SECONDS = 20 * 60


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    segments: list[Segment]
    language: str | None
    backend: str

    def text(self, timestamps: bool = True) -> str:
        if not self.segments:
            return "(no speech detected)"
        if not timestamps:
            return " ".join(s.text for s in self.segments)
        return "\n".join(f"[{fmt_time(s.start)}] {s.text}" for s in self.segments)

    def between(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.segments if s.start >= start and s.start < end]


def pick_backend() -> str:
    choice = settings.transcriber
    if choice != "auto":
        if choice not in ("local", *API_PROVIDERS):
            raise MediaError(f"Unknown CLIPSENSE_TRANSCRIBER '{choice}'. Use auto, local, groq or openai.")
        return choice
    for name, (key_var, _, _) in API_PROVIDERS.items():
        if os.environ.get(key_var):
            return name
    try:
        import faster_whisper

        return "local"
    except ImportError:
        raise MediaError(
            "No transcription backend available. Either install local Whisper "
            "(pip install 'clipsense[local]') or set GROQ_API_KEY or OPENAI_API_KEY "
            "(and install 'clipsense[api]')."
        ) from None


def transcribe(info: MediaInfo, language: str | None = None, progress: Progress | None = None) -> Transcript:
    if not info.has_audio:
        return Transcript([], None, "none")
    backend = pick_backend()
    model = settings.whisper_model if backend == "local" else (settings.api_model or API_PROVIDERS[backend][2])
    vad = "_vad" if backend == "local" and settings.vad else ""
    cache = info.work_dir / f"transcript_v2_{backend}_{model.replace('/', '_')}{vad}_{language or 'auto'}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return Transcript([Segment(**s) for s in data["segments"]], data["language"], data["backend"])

    progress = progress or (lambda f, m: None)
    if backend == "local":
        result = _transcribe_local(info, model, language, progress)
    else:
        result = _transcribe_api(info, backend, model, language, progress)
    cache.write_text(json.dumps(asdict(result)), encoding="utf-8")
    return result


_models: dict[tuple[str, str], object] = {}
_model_lock = threading.Lock()


def _load_local_model(name: str, device: str):
    from faster_whisper import WhisperModel

    with _model_lock:
        key = (name, device)
        if key not in _models:
            log.info("loading Whisper model '%s' on %s (first run downloads it)", name, device)
            _models[key] = WhisperModel(name, device=device, compute_type="auto")
        return _models[key]


def _transcribe_local(info: MediaInfo, model_name: str, language: str | None, progress: Progress) -> Transcript:
    audio = extract_audio(info, info.work_dir / "audio16k.wav")
    progress(0.0, f"Transcribing locally with Whisper '{model_name}'")
    try:
        return _run_local(audio, model_name, settings.device, language, info.duration, progress)
    except RuntimeError as e:
        if settings.device == "auto" and ("cuda" in str(e).lower() or "cublas" in str(e).lower()):
            log.warning("GPU transcription failed (%s); using CPU from now on", e)
            settings.device = "cpu"
            return _run_local(audio, model_name, "cpu", language, info.duration, progress)
        raise


def _run_local(audio, model_name, device, language, duration, progress) -> Transcript:
    model = _load_local_model(model_name, device)
    seg_iter, meta = model.transcribe(read_wav(audio), language=language, vad_filter=settings.vad)
    segments = []
    for s in seg_iter:
        end = min(s.end, duration) if duration else s.end
        segments.append(Segment(round(s.start, 2), round(end, 2), s.text.strip()))
        if duration:
            progress(end / duration, f"Transcribed {fmt_time(end)} of {fmt_time(duration)}")
    return Transcript(segments, meta.language, f"local:{model_name}")


def _transcribe_api(info: MediaInfo, provider: str, model: str, language: str | None, progress: Progress) -> Transcript:
    try:
        from openai import OpenAI
    except ImportError:
        raise MediaError("API transcription needs the 'api' extra: pip install 'clipsense[api]'") from None
    key_var, base_url, _ = API_PROVIDERS[provider]
    key = os.environ.get(key_var)
    if not key:
        raise MediaError(f"CLIPSENSE_TRANSCRIBER is '{provider}' but {key_var} isn't set.")
    client = OpenAI(api_key=key, base_url=base_url)

    duration = info.duration or 0
    starts = [0.0] if duration <= API_CHUNK_SECONDS else [
        float(t) for t in range(0, int(duration) + 1, API_CHUNK_SECONDS) if t < duration
    ]
    segments: list[Segment] = []
    detected = None
    for i, offset in enumerate(starts):
        progress(i / len(starts), f"Uploading audio chunk {i + 1}/{len(starts)} to {provider}")
        length = API_CHUNK_SECONDS if len(starts) > 1 else None
        chunk = extract_audio(info, info.work_dir / f"audio_{int(offset)}.m4a", offset, length)
        with open(chunk, "rb") as f:
            kwargs = {"model": model, "file": f, "response_format": "verbose_json"}
            if language:
                kwargs["language"] = language
            r = client.audio.transcriptions.create(**kwargs)
        detected = detected or getattr(r, "language", None)
        segs = getattr(r, "segments", None) or []
        if not segs and getattr(r, "text", "").strip():
            segs = [{"start": 0, "end": length or duration, "text": r.text}]
        for s in segs:
            get = s.get if isinstance(s, dict) else lambda k, s=s: getattr(s, k)
            text = (get("text") or "").strip()
            if text:
                segments.append(Segment(round(get("start") + offset, 2), round(get("end") + offset, 2), text))
    progress(1.0, "Transcription done")
    return Transcript(segments, detected, f"{provider}:{model}")
