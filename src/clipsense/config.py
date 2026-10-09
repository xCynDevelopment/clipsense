from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    transcriber: str = field(default_factory=lambda: _env("CLIPSENSE_TRANSCRIBER", "auto").lower())
    whisper_model: str = field(default_factory=lambda: _env("CLIPSENSE_WHISPER_MODEL", "base"))
    device: str = field(default_factory=lambda: _env("CLIPSENSE_DEVICE", "auto"))
    vad: bool = field(default_factory=lambda: _env_bool("CLIPSENSE_VAD", False))
    sounds: str = field(default_factory=lambda: _env("CLIPSENSE_SOUNDS", "auto").lower())
    api_model: str = field(default_factory=lambda: _env("CLIPSENSE_API_MODEL"))
    cache_dir: Path = field(
        default_factory=lambda: Path(_env("CLIPSENSE_CACHE_DIR") or Path(tempfile.gettempdir()) / "clipsense")
    )
    max_duration_s: float = field(default_factory=lambda: float(_env("CLIPSENSE_MAX_MINUTES", "180")) * 60)
    max_download_bytes: int = field(default_factory=lambda: int(float(_env("CLIPSENSE_MAX_DOWNLOAD_MB", "2000")) * 1e6))
    max_frames: int = field(default_factory=lambda: int(_env("CLIPSENSE_MAX_FRAMES", "30")))
    allow_local_files: bool = field(default_factory=lambda: _env_bool("CLIPSENSE_ALLOW_LOCAL_FILES", True))
    allow_private_urls: bool = field(default_factory=lambda: _env_bool("CLIPSENSE_ALLOW_PRIVATE_URLS", True))

    def harden_for_public(self) -> None:
        self.allow_local_files = _env_bool("CLIPSENSE_ALLOW_LOCAL_FILES", False)
        self.allow_private_urls = _env_bool("CLIPSENSE_ALLOW_PRIVATE_URLS", False)


settings = Settings()
