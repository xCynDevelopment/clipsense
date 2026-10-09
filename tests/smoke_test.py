import asyncio
import sys
import time
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

SOURCE = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).with_name("demo.mp4"))


def show(name: str, res, started: float) -> None:
    print(f"\n== {name}  ({time.time() - started:.1f}s){'  ERROR' if res.is_error else ''}")
    for c in res.content:
        if c.type == "text":
            print("  " + c.text.replace("\n", "\n  "))
        else:
            print(f"  [{c.type}: {c.mime_type}, {len(c.data) * 3 // 4 // 1024} KB]")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "clipsense", "serve"])
    async with Client(params) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(t.name for t in tools.tools))
        print("source:", SOURCE)

        t = time.time()
        show("media_info", await client.call_tool("media_info", {"source": SOURCE}), t)
        t = time.time()
        show("watch_video", await client.call_tool("watch_video", {"source": SOURCE, "frames": 4}), t)
        t = time.time()
        show("look_closer", await client.call_tool("look_closer", {"source": SOURCE, "start": "0:03", "end": "0:08", "count": 2}), t)
        t = time.time()
        show("look_closer filmstrip", await client.call_tool("look_closer", {"source": SOURCE, "start": "0:03", "end": "0:08", "filmstrip": True}), t)
        t = time.time()
        show("transcribe_media (cached)", await client.call_tool("transcribe_media", {"source": SOURCE}), t)
        t = time.time()
        show("bad path", await client.call_tool("media_info", {"source": "Z:/does/not/exist.mp4"}), t)


asyncio.run(main())
