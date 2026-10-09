from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from pathlib import Path

import anyio
import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, sounds
from .config import settings
from .media import MediaError, MediaInfo, extract_frame, fmt_time, frame_times, open_media
from .media import filmstrip as make_filmstrip
from .sounds import SoundEvent
from .transcribe import Transcript, transcribe

log = logging.getLogger("clipsense")

INSTRUCTIONS = """\
clipsense lets you watch videos and listen to audio. `source` is a local file path or a URL \
(direct media links, and YouTube and other video sites when supported).

- Start with `watch_video`: it returns the transcript interleaved with frames chosen at scene \
changes, plus non-speech sounds (music, laughter, applause, animals...), so you can connect what \
is said, shown and heard at each moment.
- To check a detail (on-screen text, who is speaking), call `look_closer` on a narrow time range \
rather than guessing. For fast action, use `look_closer` with `filmstrip=true`.
- Use `transcribe_media` for podcasts, voice notes and other audio-only media.
- Cite moments by timestamp (mm:ss) when you answer.
"""

mcp = MCPServer("clipsense", instructions=INSTRUCTIONS, version=__version__)


def parse_time(value: float | str | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    parts = str(value).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise MediaError(f"Couldn't read the time '{value}'. Use seconds (83) or mm:ss (1:23).") from None
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def _lines(
    transcript: Transcript, events: list[SoundEvent], start: float, end: float, sounds_from: float | None = None
) -> str | None:
    said = [f"[{fmt_time(s.start)}] {s.text}" for s in transcript.between(start, end)]
    heard = sorted({e.label for e in sounds.overlapping(events, start if sounds_from is None else sounds_from, end)})
    if heard:
        said.append("Sounds: " + ", ".join(heard))
    return "\n".join(said) or None


def _starting(events: list[SoundEvent], start: float, end: float) -> list[SoundEvent]:
    return [e for e in events if start <= e.start < end]


def build_timeline(
    info: MediaInfo, transcript: Transcript, times: list[float], max_width: int, events: list[SoundEvent] = ()
) -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    duration = info.duration or 0
    if times and (before := _lines(transcript, [], 0, times[0])):
        items.append(("text", before))
    for i, t in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else duration + 1
        frame = extract_frame(info, t, max_width)
        if frame:
            items.append(("image", (t, frame)))
        text = _lines(transcript, _starting(events, 0 if i == 0 else t, nxt), t, nxt, sounds_from=0 if i == 0 else t)
        items.append(("text", text or "(no speech)"))
    return items


def _progress_reporter(ctx: Context | None) -> Callable[[float, str], None]:
    def report(fraction: float, message: str) -> None:
        if ctx is None:
            return
        try:
            anyio.from_thread.run(functools.partial(ctx.report_progress, fraction * 100, 100, message))
        except Exception:
            pass

    return report


async def _in_thread(fn, *args, **kwargs):
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except MediaError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        log.exception("tool failed")
        detail = f"{type(e).__name__}: {e}" if settings.allow_local_files else "internal error"
        raise ToolError(f"Processing failed ({detail}).") from e


def _clamp_frames(n: int) -> int:
    return max(1, min(int(n), settings.max_frames))


def _detect_sounds(info: MediaInfo, progress) -> list[SoundEvent]:
    try:
        return sounds.detect_sounds(info, progress)
    except Exception:
        log.exception("sound detection failed")
        return []


@mcp.tool(structured_output=False)
async def watch_video(
    source: str,
    frames: int = 12,
    language: str | None = None,
    ctx: Context | None = None,
) -> list:
    """Watch and listen to a video: returns the full timestamped transcript interleaved with frames
    picked at scene changes, plus non-speech sounds (music, laughter, applause, animals, vehicles...),
    so you can connect what is said, shown and heard.

    Args:
        source: Local file path, or an http(s) URL to a media file or a video page (YouTube, etc.).
        frames: How many frames to look at across the whole video (1-30). Use more for long or
            visually busy videos.
        language: Spoken language code like "en" or "es". Leave empty to auto-detect.
    """

    def work():
        progress = _progress_reporter(ctx)
        progress(0.0, "Loading media")
        info = open_media(source)
        transcript = transcribe(info, language, progress)
        events = _detect_sounds(info, progress)
        if not info.has_video:
            return info, transcript, events, []
        progress(0.95, "Picking frames")
        times = frame_times(info, _clamp_frames(frames))
        return info, transcript, events, build_timeline(info, transcript, times, 768, events)

    info, transcript, events, timeline = await _in_thread(work)
    header = (
        f"Media: {source if source.startswith('http') else Path(source).name}\n"
        f"Length {fmt_time(info.duration or 0)}"
        + (f", {info.width}x{info.height}" if info.width else "")
        + f". Speech: {transcript.language or 'none'} (transcribed with {transcript.backend})."
    )
    if events:
        header += "\nSounds heard:\n" + sounds.summary(events)
    if not info.has_video:
        return [header + "\nAudio only, no picture.\n\nTranscript:\n" + transcript.text()]
    out: list = [header + "\n\nTimeline (each frame is followed by what is said and heard until the next frame):"]
    for kind, value in timeline:
        if kind == "image":
            t, path = value
            out.append(f"--- Frame at {fmt_time(t)} ---")
            out.append(Image(path=path))
        else:
            out.append(value)
    return out


@mcp.tool(structured_output=False)
async def look_closer(
    source: str,
    start: str | None = None,
    end: str | None = None,
    count: int | None = None,
    at: list[str] | None = None,
    high_res: bool = False,
    filmstrip: bool = False,
    ctx: Context | None = None,
) -> list:
    """Look closely at part of a video: frames from a time range, or at exact moments, plus what
    is said and heard there. Use it to read on-screen text, check a detail, or follow fast action.

    Args:
        source: Same path or URL as before (downloads are cached, so this is fast).
        start: Start of the range, in seconds ("83") or mm:ss ("1:23"). Default: beginning.
        end: End of the range. Default: end of the video.
        count: Number of frames across the range (default 6, or 12 with filmstrip; max 30).
        at: Exact timestamps to grab instead of a range, e.g. ["0:42", "1:05"].
        high_res: Larger frames (up to 1280px wide) for reading small text.
        filmstrip: Pack the frames into one grid image (read left to right, top to bottom) to
            follow motion: a punch, a trick, a goal. Best on a short range of a few seconds.
    """

    def work():
        info = open_media(source)
        if not info.has_video:
            raise MediaError("This file has no video track; use `transcribe_media` instead.")
        if at:
            times = sorted(parse_time(t) for t in at[: settings.max_frames])
        else:
            n = count or (12 if filmstrip else 6)
            if filmstrip and n > 4:
                n = n // 4 * 4
            times = frame_times(info, _clamp_frames(n), parse_time(start), parse_time(end), smart=not filmstrip)
        lo, hi = times[0], times[-1]
        progress = _progress_reporter(ctx)
        transcript = transcribe(info, None, progress) if info.has_audio else Transcript([], None, "none")
        events = _detect_sounds(info, progress)
        context = _lines(transcript, events, max(0, lo - 5), hi + 5)
        if filmstrip:
            return "strip", make_filmstrip(info, times), context
        width = 1280 if high_res else 768
        return "frames", [(t, extract_frame(info, t, width)) for t in times], context

    kind, payload, context = await _in_thread(work)
    out: list = []
    if kind == "strip":
        if payload is None:
            raise ToolError("Couldn't grab frames in that range; it may be past the end of the video.")
        path, times = payload
        step = (times[-1] - times[0]) / max(len(times) - 1, 1)
        out.append(
            f"Filmstrip: {len(times)} frames from {fmt_time(times[0])} ({times[0]:.1f}s) to "
            f"{fmt_time(times[-1])} ({times[-1]:.1f}s), about {step:.2f}s apart. "
            "Read left to right, top to bottom."
        )
        out.append(Image(path=path))
    else:
        for t, path in payload:
            out.append(f"--- Frame at {fmt_time(t)} ({t:.1f}s) ---")
            out.append(Image(path=path) if path else "(couldn't grab a frame here; it may be past the end)")
    if context:
        out.append("Said and heard around this part:\n" + context)
    return out


@mcp.tool()
async def transcribe_media(
    source: str,
    language: str | None = None,
    timestamps: bool = True,
    include_sounds: bool = True,
    ctx: Context | None = None,
) -> str:
    """Transcribe the speech in an audio or video file (podcasts, voice notes, meetings, videos),
    and list non-speech sounds like music, laughter or applause with their times.

    Args:
        source: Local file path, or an http(s) URL to a media file or a video page.
        language: Spoken language code like "en". Leave empty to auto-detect.
        timestamps: Prefix each line with [mm:ss].
        include_sounds: Also list non-speech sounds.
    """

    def work():
        progress = _progress_reporter(ctx)
        info = open_media(source)
        transcript = transcribe(info, language, progress)
        events = _detect_sounds(info, progress) if include_sounds else []
        return info, transcript, events

    info, transcript, events = await _in_thread(work)
    text = (
        f"Length {fmt_time(info.duration or 0)}, language {transcript.language or 'unknown'}, "
        f"transcribed with {transcript.backend}.\n\n{transcript.text(timestamps)}"
    )
    if events:
        text += "\n\nSounds heard:\n" + sounds.summary(events)
    return text


@mcp.tool()
async def media_info(source: str) -> dict:
    """Quick facts about a media file or URL (length, resolution, audio/video tracks) without
    transcribing it. Useful for deciding how many frames to request.

    Args:
        source: Local file path, or an http(s) URL.
    """
    info = await _in_thread(open_media, source)
    return info.summary()
