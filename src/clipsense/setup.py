from __future__ import annotations

import glob
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

SYSTEM = platform.system()


def server_command() -> dict:
    return {"command": sys.executable, "args": ["-m", "clipsense", "serve"]}


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _desktop_config_paths() -> list[Path]:
    if SYSTEM == "Darwin":
        return [Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"]
    if SYSTEM == "Windows":
        paths = []
        for pkg in glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Packages\Claude_*")):
            paths.append(Path(pkg) / "LocalCache/Roaming/Claude/claude_desktop_config.json")
        paths.append(Path(os.path.expandvars(r"%APPDATA%")) / "Claude/claude_desktop_config.json")
        return paths
    return [Path.home() / ".config/Claude/claude_desktop_config.json"]


def _desktop_running() -> bool:
    if SYSTEM == "Windows":
        ps = (
            "Get-Process claude -ErrorAction SilentlyContinue | Where-Object { "
            "$_.Path -like '*\\WindowsApps\\Claude_*' -or $_.Path -like '*\\AnthropicClaude\\*' } | "
            "Select-Object -First 1 | ForEach-Object { 'yes' }"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        return "yes" in r.stdout
    if SYSTEM == "Darwin":
        return subprocess.run(["pgrep", "-x", "Claude"], capture_output=True).returncode == 0
    return False


def _relaunch_desktop() -> None:
    try:
        if SYSTEM == "Darwin":
            subprocess.Popen(["open", "-a", "Claude"])
        elif SYSTEM == "Windows":
            pkgs = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Packages\Claude_*"))
            squirrel = Path(os.path.expandvars(r"%LOCALAPPDATA%\AnthropicClaude\claude.exe"))
            if pkgs:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{Path(pkgs[0]).name}!Claude"])
            elif squirrel.exists():
                subprocess.Popen([str(squirrel)])
    except OSError:
        pass


def _write_entry(path: Path, entry: dict) -> None:
    data = {}
    if path.exists() and path.read_text(encoding="utf-8").strip():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise SystemExit(f"error: {path} isn't valid JSON, so I won't touch it. Fix or delete it and retry.")
        shutil.copy(path, path.with_name(path.name + ".bak"))
    data.setdefault("mcpServers", {})["clipsense"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def setup_claude_desktop(entry: dict, restart: bool) -> None:
    paths = [p for p in _desktop_config_paths() if p.parent.exists()] or _desktop_config_paths()[-1:]
    was_running = _desktop_running()
    if was_running:
        say("Claude Desktop is open. Please quit it completely:")
        if SYSTEM == "Windows":
            say("  right-click the Claude icon in the system tray (bottom-right, near the clock) -> Quit")
        else:
            say("  click Claude in the menu bar -> Quit Claude (or press Cmd+Q)")
        say("Waiting...")
        while _desktop_running():
            time.sleep(1)
        time.sleep(2)
    for p in paths:
        _write_entry(p, entry)
        say(f"  Added clipsense to {p}")
    if was_running and restart:
        say("Reopening Claude...")
        _relaunch_desktop()


def setup_claude_code(entry: dict) -> None:
    claude = shutil.which("claude")
    if not claude:
        say("  Claude Code isn't installed (no `claude` command found); skipping.")
        return
    subprocess.run([claude, "mcp", "remove", "clipsense", "--scope", "user"], capture_output=True)
    env_args = [a for k, v in entry.get("env", {}).items() for a in ("-e", f"{k}={v}")]
    r = subprocess.run(
        [claude, "mcp", "add", "--scope", "user", *env_args, "clipsense", "--", entry["command"], *entry["args"]],
        capture_output=True, text=True,
    )
    say("  Added clipsense to Claude Code" if r.returncode == 0 else f"  Claude Code setup failed: {r.stderr.strip()}")


def setup_cursor(entry: dict) -> None:
    path = Path.home() / ".cursor/mcp.json"
    _write_entry(path, entry)
    say(f"  Added clipsense to {path}")


def download_models() -> None:
    from . import sounds
    from .config import settings
    from .transcribe import pick_backend

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    try:
        if pick_backend() == "local":
            from faster_whisper.utils import download_model

            say(f"  Speech model (Whisper '{settings.whisper_model}')...")
            download_model(settings.whisper_model)
    except Exception as e:
        say(f"  Skipped the speech model ({e}); it will download on first use.")
    if sounds.available():
        from huggingface_hub import hf_hub_download

        say("  Sound model (~90 MB)...")
        hf_hub_download(sounds.MODEL_REPO, sounds.MODEL_FILE)
        hf_hub_download(sounds.MODEL_REPO, "config.json")


def run_setup(targets: list[str], groq_key: str | None, models: bool, restart: bool) -> None:
    entry = server_command()
    if groq_key:
        entry["env"] = {"GROQ_API_KEY": groq_key.strip()}
        os.environ["GROQ_API_KEY"] = groq_key.strip()
    if models:
        say("Downloading models so your first video is quick:")
        download_models()
    for target in targets:
        say(f"Setting up {target}:")
        {"claude-desktop": lambda: setup_claude_desktop(entry, restart),
         "claude-code": lambda: setup_claude_code(entry),
         "cursor": lambda: setup_cursor(entry)}[target]()
    say()
    say("All set! In a new chat, try:")
    say('  "Watch https://www.youtube.com/watch?v=GvijmN66dZQ and tell me how people kept food cold before fridges"')
    say("or give it the path of any video on your computer.")
