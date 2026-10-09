from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import settings

log = logging.getLogger("clipsense")

MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mpeg", ".mpg", ".ts",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma",
}


class MediaError(Exception):
    pass


@dataclass
class MediaInfo:
    path: Path
    work_dir: Path
    duration: float | None
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None

    def summary(self) -> dict:
        return {
            "duration_seconds": round(self.duration, 2) if self.duration else None,
            "duration": fmt_time(self.duration) if self.duration else None,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "resolution": f"{self.width}x{self.height}" if self.width else None,
            "file_size_mb": round(self.path.stat().st_size / 1e6, 2),
        }


def fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
    )


def is_url(source: str) -> bool:
    return urllib.parse.urlparse(source).scheme in ("http", "https")


def open_media(source: str) -> MediaInfo:
    source = source.strip().strip('"').strip("'")
    if is_url(source):
        path = _download(source)
    else:
        if not settings.allow_local_files:
            raise MediaError("This server doesn't accept local file paths. Pass a public http(s) URL instead.")
        if source.startswith("file://"):
            source = urllib.request.url2pathname(urllib.parse.urlparse(source).path)
        path = Path(source).expanduser()
        if not path.is_file():
            raise MediaError(f"File not found: {source}")
        path = path.resolve()

    info = probe(path)
    if not info.has_audio and not info.has_video:
        raise MediaError("That file has no audio or video streams ffmpeg can read.")
    if info.duration and info.duration > settings.max_duration_s:
        raise MediaError(
            f"Media is {fmt_time(info.duration)} long; the limit is {fmt_time(settings.max_duration_s)} "
            "(set CLIPSENSE_MAX_MINUTES to change it)."
        )
    return info


def _work_dir_for(path: Path) -> Path:
    st = path.stat()
    key = hashlib.sha256(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    d = settings.cache_dir / "work" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _check_host_is_public(url: str) -> None:
    if settings.allow_private_urls:
        return
    host = urllib.parse.urlparse(url).hostname
    if not host:
        raise MediaError(f"Invalid URL: {url}")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except socket.gaierror as e:
        raise MediaError(f"Couldn't resolve {host}") from e
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global:
            raise MediaError("URLs pointing at private or local network addresses aren't allowed.")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host_is_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str) -> Path:
    _check_host_is_public(url)
    dest_dir = settings.cache_dir / "downloads" / hashlib.sha256(url.encode()).hexdigest()[:16]
    done = dest_dir / ".done"
    if done.exists():
        return Path(done.read_text(encoding="utf-8"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    path = _download_direct(url, dest_dir) if ext in MEDIA_EXTENSIONS else None
    if path is None:
        path = _download_with_ytdlp(url, dest_dir)
    done.write_text(str(path), encoding="utf-8")
    return path


def _download_direct(url: str, dest_dir: Path) -> Path | None:
    opener = urllib.request.build_opener(_SafeRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": "clipsense/0.1"})
    with opener.open(req, timeout=30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if ctype.startswith("text/html"):
            return None
        length = int(resp.headers.get("Content-Length") or 0)
        if length > settings.max_download_bytes:
            raise MediaError(f"File is {length / 1e6:.0f} MB; the limit is {settings.max_download_bytes / 1e6:.0f} MB.")
        ext = Path(urllib.parse.urlparse(resp.url).path).suffix.lower() or ".bin"
        path = dest_dir / f"media{ext}"
        total = 0
        with open(path, "wb") as f:
            while chunk := resp.read(1 << 20):
                total += len(chunk)
                if total > settings.max_download_bytes:
                    f.close()
                    path.unlink(missing_ok=True)
                    raise MediaError(f"Download exceeded the {settings.max_download_bytes / 1e6:.0f} MB limit.")
                f.write(chunk)
    return path


class _StderrLogger:
    def debug(self, msg):
        log.debug(msg)

    def info(self, msg):
        log.debug(msg)

    def warning(self, msg):
        log.warning(msg)

    def error(self, msg):
        log.error(msg)


def _download_with_ytdlp(url: str, dest_dir: Path) -> Path:
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError:
        raise MediaError(
            "That looks like a web page, not a media file. Install the 'url' extra "
            "(pip install 'clipsense[url]') to support YouTube and other video sites."
        ) from None
    opts = {
        "outtmpl": str(dest_dir / "media.%(ext)s"),
        "format": (
            "best[height<=720][acodec!=none][vcodec!=none]"
            "/bestvideo[height<=720]+bestaudio"
            "/best[acodec!=none]/best"
        ),
        "ffmpeg_location": ffmpeg_exe(),
        "merge_output_format": "mp4",
        "js_runtimes": _js_runtimes(),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _StderrLogger(),
        "max_filesize": settings.max_download_bytes,
        "match_filter": _ytdlp_duration_filter,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except DownloadError as e:
        raise MediaError(f"Couldn't download that URL: {e}") from e
    if not path.exists():
        raise MediaError("The download didn't produce a file (it may be over the size or length limit).")
    return path


def _js_runtimes() -> dict:
    runtimes: dict = {}
    try:
        import deno

        runtimes["deno"] = {"path": deno.find_deno_bin()}
    except Exception:
        if shutil.which("deno"):
            runtimes["deno"] = {"path": None}
    if shutil.which("node"):
        runtimes["node"] = {"path": None}
    return runtimes


def _ytdlp_duration_filter(info, *, incomplete=False):
    d = info.get("duration")
    if d and d > settings.max_duration_s:
        return f"Video is {fmt_time(d)} long; the limit is {fmt_time(settings.max_duration_s)}."
    return None


def probe(path: Path) -> MediaInfo:
    err = _run_ffmpeg(["-i", str(path)]).stderr
    dur = None
    if m := re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err):
        dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    width = height = None
    has_video = False
    for line in re.findall(r"Stream #.*?Video: .*", err):
        if "attached pic" in line:
            continue
        has_video = True
        if m := re.search(r"\b(\d{2,5})x(\d{2,5})\b", line):
            width, height = int(m[1]), int(m[2])
        break
    has_audio = re.search(r"Stream #.*?Audio:", err) is not None
    return MediaInfo(path, _work_dir_for(path), dur, has_video, has_audio, width, height)


def extract_audio(info: MediaInfo, dest: Path, start: float = 0, length: float | None = None) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    args = ["-y", "-loglevel", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(info.path)]
    if length:
        args += ["-t", f"{length:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", "16000"]
    args += ["-c:a", "pcm_s16le"] if dest.suffix == ".wav" else ["-c:a", "aac", "-b:a", "32k"]
    r = _run_ffmpeg([*args, str(dest)])
    if r.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise MediaError(f"ffmpeg couldn't extract audio: {r.stderr.strip()[-500:]}")
    return dest


def read_wav(path: Path):
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _clip_range(info: MediaInfo, start: float | None, end: float | None) -> tuple[float, float]:
    dur = info.duration or 0
    lo = max(0.0, start or 0.0)
    hi = min(end, dur) if end is not None and dur else (end or dur)
    if hi <= lo:
        raise MediaError("The end time must be after the start time and within the video.")
    return lo, hi


def scene_changes(info: MediaInfo, threshold: float = 0.25) -> list[tuple[float, float]]:
    cache = info.work_dir / f"scenes_{threshold}.json"
    if cache.exists():
        return [tuple(x) for x in json.loads(cache.read_text(encoding="utf-8"))]
    err = _run_ffmpeg([
        "-i", str(info.path), "-an",
        "-vf", f"scale=160:-2,select='gt(scene,{threshold})',metadata=print",
        "-f", "null", "-",
    ]).stderr
    cuts = [
        (float(t), float(s))
        for t, s in re.findall(r"pts_time:([\d.]+)\s*\n.*?lavfi\.scene_score=([\d.]+)", err)
    ]
    cache.write_text(json.dumps(cuts), encoding="utf-8")
    return cuts


def frame_times(
    info: MediaInfo, count: int, start: float | None = None, end: float | None = None, smart: bool = True
) -> list[float]:
    lo, hi = _clip_range(info, start, end)
    step = (hi - lo) / count
    cuts = [c for c in scene_changes(info) if lo <= c[0] < hi] if smart and info.has_video else []
    times = []
    for i in range(count):
        a, b = lo + step * i, lo + step * (i + 1)
        in_slot = [c for c in cuts if a <= c[0] < b]
        if in_slot:
            t = max(in_slot, key=lambda c: c[1])[0] + 0.4
            times.append(min(t, b - 0.05))
        else:
            times.append(a + step / 2)
    return [round(t, 2) for t in times]


def filmstrip(info: MediaInfo, times: list[float], cols: int = 4, tile_width: int = 320) -> tuple[Path, list[float]] | None:
    shots = [(t, p) for t in times if (p := extract_frame(info, t, tile_width))]
    if not shots:
        return None
    cols = min(cols, len(shots))
    rows = -(-len(shots) // cols)
    shots += [shots[-1]] * (rows * cols - len(shots))
    key = hashlib.sha256(repr([t for t, _ in shots]).encode()).hexdigest()[:12]
    dest = info.work_dir / f"strip_{key}_{tile_width}.jpg"
    if not dest.exists():
        listing = info.work_dir / f"strip_{key}.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for _, p in shots), encoding="utf-8")
        r = _run_ffmpeg([
            "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
            "-vf", f"tile={cols}x{rows}:padding=4:color=white", "-frames:v", "1", "-q:v", "4", str(dest),
        ])
        if r.returncode != 0 or not dest.exists():
            raise MediaError(f"ffmpeg couldn't build the filmstrip: {r.stderr.strip()[-300:]}")
    return dest, [t for t, _ in shots]


def extract_frame(info: MediaInfo, t: float, max_width: int = 768) -> Path | None:
    dest = info.work_dir / f"frame_{t:010.2f}_{max_width}.jpg"
    if dest.exists():
        return dest
    r = _run_ffmpeg([
        "-y", "-loglevel", "error",
        "-ss", f"{t:.3f}", "-i", str(info.path),
        "-frames:v", "1",
        "-vf", f"scale='min({max_width},iw)':-2",
        "-q:v", "4",
        str(dest),
    ])
    if r.returncode != 0 or not dest.exists():
        log.warning("no frame at %.2fs: %s", t, r.stderr.strip()[-300:])
        return None
    return dest
