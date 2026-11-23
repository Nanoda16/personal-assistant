"""MCP stdio 冒烟测试脚本：验证工具数量 >= 17 且能正常调用 stats。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _run(pa_home: str) -> None:
    env = dict(os.environ)
    env["PA_HOME"] = pa_home
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "personal_assistant.server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_res = await session.list_tools()
            names = [t.name for t in tools_res.tools]
            print(f"tools_count={len(names)}")
            print("tools=", ", ".join(names))
            assert len(names) >= 17, f"工具数量不足 17: {len(names)}"

            stats_res = await session.call_tool("stats", {})
            text = stats_res.content[0].text
            print("stats=", text)
            data = json.loads(text)
            assert "active_memories" in data


def main() -> None:
    pa_home = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[1] / "data")
    asyncio.run(_run(pa_home))


if __name__ == "__main__":
    main()
