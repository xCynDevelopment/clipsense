from __future__ import annotations

import argparse
import hmac
import logging
import os
import shutil
import sys
from pathlib import Path

from .config import settings
from .media import MediaError, fmt_time, frame_times, open_media


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("clipsense").setLevel(os.environ.get("CLIPSENSE_LOG", "INFO"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    parser = argparse.ArgumentParser(prog="clipsense", description="Let AI models watch and listen to video and audio.")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Run the MCP server (default: stdio, for Claude Desktop / Claude Code / Cursor).")
    serve.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio (for hosting).")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))

    digest = sub.add_parser("digest", help="Write a transcript and key frames to a folder you can upload to any AI chat.")
    digest.add_argument("source", help="Video/audio file path or URL")
    digest.add_argument("-o", "--out", help="Output folder (default: <name>_digest next to the file, or ./clipsense_digest)")
    digest.add_argument("-n", "--frames", type=int, default=12, help="Number of frames (default 12)")
    digest.add_argument("-l", "--language", help="Spoken language code, e.g. en (default: auto-detect)")

    setup = sub.add_parser("setup", help="Connect clipsense to Claude Desktop (default), Claude Code or Cursor.")
    setup.add_argument("apps", nargs="*", default=["claude-desktop"],
                       choices=["claude-desktop", "claude-code", "cursor"], metavar="APP",
                       help="claude-desktop (default), claude-code, cursor")
    setup.add_argument("--groq-key", help="Use Groq for fast, accurate transcription (free key at console.groq.com)")
    setup.add_argument("--skip-models", action="store_true", help="Don't pre-download the speech and sound models")
    setup.add_argument("--no-restart", action="store_true", help="Don't reopen Claude Desktop afterwards")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "setup":
            from .setup import run_setup

            run_setup(args.apps, args.groq_key, not args.skip_models, not args.no_restart)
        elif args.cmd == "digest":
            run_digest(args.source, args.out, args.frames, args.language)
        elif args.cmd == "serve" and args.http:
            run_http(args.host, args.port)
        else:
            from .server import mcp

            mcp.run("stdio")
    except MediaError as e:
        sys.exit(f"error: {e}")


def run_digest(source: str, out: str | None, frames: int, language: str | None) -> None:
    from . import sounds
    from .server import build_timeline
    from .transcribe import transcribe

    def progress(frac: float, msg: str) -> None:
        print(f"\r  {msg} ({frac:.0%})".ljust(70), end="", file=sys.stderr, flush=True)

    print(f"Opening {source}", file=sys.stderr)
    info = open_media(source)
    if out:
        out_dir = Path(out)
    elif not source.startswith("http"):
        out_dir = Path(source).with_name(Path(source).stem + "_digest")
    else:
        out_dir = Path("clipsense_digest")
    (out_dir / "frames").mkdir(parents=True, exist_ok=True)

    transcript = transcribe(info, language, progress)
    events = sounds.detect_sounds(info, progress)
    print(file=sys.stderr)
    text = transcript.text()
    if events:
        text += "\n\nSounds heard:\n" + sounds.summary(events)
    (out_dir / "transcript.txt").write_text(text, encoding="utf-8")

    md = [
        f"# {info.path.name}",
        f"Length {fmt_time(info.duration or 0)}, speech language: {transcript.language or 'none'}.",
        "",
    ]
    if events:
        md += ["Sounds heard:", sounds.summary(events), ""]
    md += ["Timeline: each frame is followed by what is said and heard until the next frame.", ""]
    if info.has_video:
        times = frame_times(info, max(1, min(frames, settings.max_frames)))
        for kind, value in build_timeline(info, transcript, times, 1024, events):
            if kind == "image":
                t, path = value
                name = f"frame_{fmt_time(t).replace(':', '-')}.jpg"
                shutil.copy(path, out_dir / "frames" / name)
                md += [f"## {fmt_time(t)}", f"![frame at {fmt_time(t)}](frames/{name})", ""]
            else:
                md += [value, ""]
    else:
        md += [transcript.text()]
    (out_dir / "timeline.md").write_text("\n".join(md), encoding="utf-8")

    print(f"Done: {out_dir.resolve()}", file=sys.stderr)
    print("  Upload timeline.md (or transcript.txt) and the images in frames/ to ChatGPT, Gemini, Claude or any", file=sys.stderr)
    print("  other AI chat, and ask about the video.", file=sys.stderr)


class TokenAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") != "/health":
            headers = dict(scope.get("headers") or [])
            given = b""
            auth = headers.get(b"authorization", b"")
            if auth.lower().startswith(b"bearer "):
                given = auth[7:].strip()
            else:
                from urllib.parse import parse_qs

                given = (parse_qs(scope.get("query_string", b"").decode()).get("key") or [""])[0].encode()
            if not hmac.compare_digest(given, self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def run_http(host: str, port: int) -> None:
    import uvicorn
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from .server import mcp

    settings.harden_for_public()
    app = mcp.streamable_http_app(host=host)
    app.router.routes.append(Route("/health", lambda request: PlainTextResponse("ok")))

    token = os.environ.get("CLIPSENSE_TOKEN", "")
    if token:
        app = TokenAuth(app, token)
    elif host not in ("127.0.0.1", "localhost", "::1"):
        logging.warning("CLIPSENSE_TOKEN is not set: anyone who finds this URL can use your server and your API key.")
    logging.info("clipsense MCP endpoint: http://%s:%d/mcp", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
