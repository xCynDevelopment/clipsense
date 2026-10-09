# clipsense

**Let Claude and other AI apps actually watch and listen to videos.**

Send your AI a YouTube link or a video file, and it gets what a person gets from watching:

- 🗣️ **Everything that's said**, with timestamps
- 🎬 **What's on screen**: a frame from each new shot
- 🔊 **The other sounds**: laughter, music, applause, a dog barking, an AI-sounding voice
- ⏪ **Rewind**: it can zoom in on any moment, or flip through fast action frame by frame

All of it is lined up on one timeline, so the AI knows what was shown and heard *while* each thing was said.

```
[00:12] So I told him the wifi password was "incorrect"...
--- Frame at 00:15 ---   🖼️
Sounds: Laughter, Applause
[00:18] Thank you, thank you.
```

---

## Install

### Windows

1. Download **[clipsense-installer.exe](https://github.com/xCynDevelopment/clipsense/releases/latest/download/clipsense-installer.exe)**.
2. Double-click it.
   - If you see **"Windows protected your PC"**, click **More info**, then **Run anyway**. This appears for new apps
     that aren't code-signed.
3. Follow the steps. When it asks, quit Claude, and it reopens by itself when done.

### Mac

1. Download **[clipsense-installer-mac.zip](https://github.com/xCynDevelopment/clipsense/releases/latest/download/clipsense-installer-mac.zip)** and open it.
2. **Right-click** `clipsense-installer` and choose **Open**, then **Open** again.
   A normal double-click is blocked the first time because the app isn't from the App Store.
3. Follow the steps. When it asks, quit Claude, and it reopens by itself when done.

### Or use the terminal

If you're comfortable with a terminal, install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```bash
uv tool install --python 3.12 "clipsense[all]"
```

```bash
clipsense setup
```

`clipsense setup` connects it to Claude Desktop. Add `claude-code` or `cursor` to connect those too, and add
`--groq-key YOUR_KEY` to use Groq.

---

## Use it

Open Claude, start a **new chat**, and give it a link or a file:

> Watch https://www.youtube.com/watch?v=GvijmN66dZQ and tell me how people kept food cold before fridges

> What's the funniest moment in C:\Users\me\Videos\party.mp4?

> Summarize this lecture and list every formula written on the board: https://youtu.be/...

In Claude Desktop you can check it's on with the **+** button under the message box → **Connectors** → **clipsense**.

It works with YouTube and most video sites, direct links to video or audio files, and files on your computer
(mp4, mov, mkv, webm, mp3, wav, m4a and more). Each video is processed once and remembered, so follow-up
questions are instant.

## Free or faster?

The installer asks how speech should be turned into text:

| | Free, on your computer | Groq |
| --- | --- | --- |
| Cost | Free | Free tier, then very cheap |
| Privacy | Audio never leaves your computer | Audio is sent to Groq |
| Speed | About 1–2 minutes per 10 minutes of video | A few seconds |
| Accuracy | Good, but it can mishear names | Excellent |

Get a Groq key at [console.groq.com/keys](https://console.groq.com/keys). To switch later, run the installer again.

Sound detection and frames always run on your computer.

---

## Other apps

clipsense is an [MCP](https://modelcontextprotocol.io) server, so it works with any app that supports MCP. After
installing, point the app at:

```json
{
  "mcpServers": {
    "clipsense": { "command": "clipsense", "args": ["serve"] }
  }
}
```

**No MCP support?** (ChatGPT, Gemini, ...) Turn a video into files you can upload to any chat:

```bash
clipsense digest my_video.mp4
```

This creates `my_video_digest/` with the timeline, transcript and frames.

## What the AI gets

| Tool | What it does |
| --- | --- |
| `watch_video` | The whole timeline: transcript, a frame at each scene change, and the sounds in between. |
| `look_closer` | Zoom in on a time range or exact moments. Use `high_res` for small text, or `filmstrip` to pack a fast moment into one grid image. |
| `transcribe_media` | Transcript and sounds, for podcasts, voice notes and meetings. |
| `media_info` | Length, resolution and tracks. |

Speech uses [Whisper](https://github.com/SYSTRAN/faster-whisper) (locally, or via Groq or OpenAI). Sounds use MIT's
[Audio Spectrogram Transformer](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593), which recognises 527
kinds of sound. Videos are downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Settings

Optional environment variables (add them under `"env"` in your app's MCP config):

| Variable | Default | |
| --- | --- | --- |
| `GROQ_API_KEY` / `OPENAI_API_KEY` | | Use an API for transcription. |
| `CLIPSENSE_TRANSCRIBER` | `auto` | `auto`, `local`, `groq` or `openai`. |
| `CLIPSENSE_WHISPER_MODEL` | `base` | Local model: `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo`. Bigger models are more accurate but slower. |
| `CLIPSENSE_DEVICE` | `auto` | `auto`, `cpu` or `cuda`. |
| `CLIPSENSE_VAD` | `off` | Skip silent parts before transcribing. Faster on long, quiet recordings, but it can drop shouting and children's speech. |
| `CLIPSENSE_SOUNDS` | `auto` | `off` disables sound detection. |
| `CLIPSENSE_MAX_MINUTES` | `180` | Longest video accepted. |
| `CLIPSENSE_MAX_FRAMES` | `30` | Most frames per request. |
| `CLIPSENSE_CACHE_DIR` | system temp | Where downloads and results are kept. |

## Uninstall

```bash
uv tool uninstall clipsense
```

Then remove the `clipsense` entry from your app's MCP config. In Claude Desktop: Settings → Developer → Edit Config.

## Hosting (advanced)

`clipsense serve --http` runs a web MCP endpoint at `/mcp`, so ChatGPT and claude.ai can connect to it by URL. In
this mode only public URLs are accepted, never local files.

```bash
docker build -t clipsense .
docker run -p 8000:8000 -e CLIPSENSE_TOKEN=change-me -e GROQ_API_KEY=gsk_... clipsense
```

Always set `CLIPSENSE_TOKEN`. Clients connect with `https://your-host/mcp?key=<token>` or an
`Authorization: Bearer <token>` header.

## Development

```bash
uv sync --all-extras
uv run python tests/smoke_test.py [video path or URL]
uv run python installer/clipsense_installer.py
```

## License

MIT
