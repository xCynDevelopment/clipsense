from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from .config import settings
from .media import MediaInfo, extract_audio, fmt_time, read_wav

log = logging.getLogger("clipsense")

MODEL_REPO = "Xenova/ast-finetuned-audioset-10-10-0.4593"
MODEL_FILE = "onnx/model_quantized.onnx"

SAMPLE_RATE = 16000
FRAME_LEN, FRAME_SHIFT, N_FFT, N_MELS = 400, 160, 512, 128
MAX_FRAMES = 1024
WINDOW_SAMPLES = FRAME_LEN + FRAME_SHIFT * (MAX_FRAMES - 1)
FEAT_MEAN, FEAT_STD = -4.2677393, 4.5689974

HOP_SECONDS = 5.0
MAX_WINDOWS = 360
THRESHOLD = 0.25
TOP_K = 3
STRICT = {"Speech synthesizer": 0.5}
RENAME = {"Speech synthesizer": "Synthetic voice (sounds computer-generated)"}

IGNORE = {
    "Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
    "Conversation", "Narration, monologue", "Silence", "Inside, small room",
    "Inside, large room or hall", "Inside, public space", "Outside, urban or manmade",
    "Outside, rural or natural", "Reverberation", "Echo", "Sound effect", "Noise", "Static",
    "Mains hum", "Hum", "Environmental noise",
}


@dataclass
class SoundEvent:
    start: float
    end: float
    label: str
    score: float

    def describe(self) -> str:
        return f"[{fmt_time(self.start)}-{fmt_time(self.end)}] {self.label}"


def available() -> bool:
    if settings.sounds == "off":
        return False
    try:
        import huggingface_hub
        import numpy
        import onnxruntime
    except ImportError:
        return False
    return True


_mel_cache = None


def _mel_banks():
    global _mel_cache
    if _mel_cache is None:
        import numpy as np

        def mel(f):
            return 1127.0 * np.log(1.0 + f / 700.0)

        n_bins = N_FFT // 2
        lo, hi = mel(20.0), mel(SAMPLE_RATE / 2)
        delta = (hi - lo) / (N_MELS + 1)
        fft_mel = mel(np.arange(n_bins) * SAMPLE_RATE / N_FFT)
        left = lo + np.arange(N_MELS)[:, None] * delta
        center, right = left + delta, left + 2 * delta
        up = (fft_mel - left) / (center - left)
        down = (right - fft_mel) / (right - center)
        banks = np.maximum(0.0, np.minimum(up, down))
        _mel_cache = np.pad(banks, ((0, 0), (0, 1))).astype(np.float32)
    return _mel_cache


def _fbank(samples):
    import numpy as np

    if len(samples) < FRAME_LEN:
        samples = np.pad(samples, (0, FRAME_LEN - len(samples)))
    n_frames = 1 + (len(samples) - FRAME_LEN) // FRAME_SHIFT
    idx = np.arange(FRAME_LEN)[None, :] + FRAME_SHIFT * np.arange(n_frames)[:, None]
    frames = samples[idx].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    prev = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = frames - 0.97 * prev
    n = np.arange(FRAME_LEN)
    frames *= 0.5 - 0.5 * np.cos(2 * np.pi * n / (FRAME_LEN - 1))
    power = np.abs(np.fft.rfft(frames, n=N_FFT)) ** 2
    feats = np.log(np.maximum(power @ _mel_banks().T, np.finfo(np.float32).eps)).astype(np.float32)
    if len(feats) < MAX_FRAMES:
        feats = np.pad(feats, ((0, MAX_FRAMES - len(feats)), (0, 0)))
    else:
        feats = feats[:MAX_FRAMES]
    return (feats - FEAT_MEAN) / (FEAT_STD * 2)


_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download

            log.info("loading sound model (first run downloads ~90 MB)")
            path = hf_hub_download(MODEL_REPO, MODEL_FILE)
            with open(hf_hub_download(MODEL_REPO, "config.json"), encoding="utf-8") as f:
                id2label = json.load(f)["id2label"]
            labels = [id2label[str(i)] for i in range(len(id2label))]
            session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
            _model = (session, session.get_inputs()[0].name, labels)
        return _model


def detect_sounds(info: MediaInfo, progress: Callable[[float, str], None] | None = None) -> list[SoundEvent]:
    if not info.has_audio or not available():
        return []
    cache = info.work_dir / "sounds_v2.json"
    if cache.exists():
        return [SoundEvent(**e) for e in json.loads(cache.read_text(encoding="utf-8"))]

    import numpy as np

    progress = progress or (lambda f, m: None)
    audio = read_wav(extract_audio(info, info.work_dir / "audio16k.wav"))
    duration = len(audio) / SAMPLE_RATE
    hop = max(HOP_SECONDS, duration / MAX_WINDOWS)
    starts = list(np.arange(0.0, max(duration - 1.0, 0.0) + 1e-6, hop)) or [0.0]
    session, input_name, labels = _load_model()

    hits: dict[str, list[list[float]]] = {}
    batch = 8
    for b in range(0, len(starts), batch):
        progress(b / len(starts), f"Listening for sounds {fmt_time(starts[b])} of {fmt_time(duration)}")
        group = starts[b : b + batch]
        feats = np.stack([_fbank(audio[int(s * SAMPLE_RATE) : int(s * SAMPLE_RATE) + WINDOW_SAMPLES]) for s in group])
        logits = session.run(None, {input_name: feats})[0]
        probs = 1 / (1 + np.exp(-logits))
        for s, p in zip(group, probs):
            end = min(s + WINDOW_SAMPLES / SAMPLE_RATE, duration)
            ranked = [i for i in np.argsort(p)[::-1] if labels[i] not in IGNORE][:TOP_K]
            for i in ranked:
                if p[i] < THRESHOLD:
                    break
                if p[i] < STRICT.get(labels[i], 0):
                    continue
                spans = hits.setdefault(RENAME.get(labels[i], labels[i]), [])
                if spans and s <= spans[-1][1]:
                    spans[-1][1] = end
                    spans[-1][2] = max(spans[-1][2], float(p[i]))
                else:
                    spans.append([float(s), float(end), float(p[i])])

    events = sorted(
        (SoundEvent(round(a, 1), round(e, 1), label, round(sc, 2)) for label, spans in hits.items() for a, e, sc in spans),
        key=lambda ev: (ev.start, -ev.score),
    )
    cache.write_text(json.dumps([asdict(e) for e in events]), encoding="utf-8")
    progress(1.0, "Sound detection done")
    return events


def overlapping(events: list[SoundEvent], start: float, end: float) -> list[SoundEvent]:
    return [e for e in events if e.start < end and e.end > start]


def summary(events: list[SoundEvent]) -> str:
    return "\n".join(e.describe() for e in events) if events else "(no notable non-speech sounds)"
