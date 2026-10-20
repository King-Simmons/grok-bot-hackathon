"""Inspector-style test: list tools and call both over stdio (and HTTP if --http URL given)."""
import asyncio, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
if sys.path and os.path.abspath(sys.path[0]) == HERE: sys.path.pop(0)
from mcp import Client
from mcp.client.stdio import StdioServerParameters

URL = sys.argv[1] if len(sys.argv) > 1 else "https://docs.x.ai"


def show(res):
    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    if sc is None:
        sc = json.loads(res.content[0].text)
    return sc.get("result", sc) if isinstance(sc, dict) else sc


async def run(target):
    async with Client(target) as c:
        tools = await c.list_tools()
        print("tools:", [t.name for t in tools.tools])
        t = time.time(); m = show(await c.call_tool("get_mood_analysis", {"url": URL}))
        print(f"get_mood_analysis {time.time()-t:.2f}s:", json.dumps({k: m[k] for k in ("sentiment", "musical_attributes")}))
        t = time.time(); r = show(await c.call_tool("synthesize_url", {"url": URL}))
        print(f"synthesize_url {time.time()-t:.2f}s:", r["track_url"], os.path.exists(r["track_path"]), sorted(r.keys()))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2].startswith("http"):
        asyncio.run(run(sys.argv[2]))
    else:
        asyncio.run(run(StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "lofi_mcp_server.py")])))
