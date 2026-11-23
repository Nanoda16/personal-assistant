"""Antigravity 生命周期 Hook 入口（保持极度轻量，禁止导入重型依赖）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 兜底：保证以任意工作目录运行都能找到包根目录
_SRC_ROOT = Path(__file__).resolve().parents[1]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))


def _resolve_pa_home(override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env_val = os.environ.get("PA_HOME", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return (Path.home() / ".personal-assistant").resolve()


def _handle_stop(pa_home: Path) -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    cid = str(payload.get("conversationId", "")).strip()
    tpath = str(payload.get("transcriptPath", "")).strip()
    if not cid and not tpath:
        return {}

    pa_home.mkdir(parents=True, exist_ok=True)
    queue_file = pa_home / "queue.jsonl"
    record = {
        "conversationId": cid,
        "transcriptPath": tpath,
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with queue_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {}


def _handle_session_start(pa_home: Path) -> dict:
    # 消费 stdin（部分宿主会发送元数据），避免管道阻塞
    if not sys.stdin.isatty():
        sys.stdin.read()

    compact_file = pa_home / "profile_compact.md"
    if not compact_file.is_file():
        return {}
    text = compact_file.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return {}

    msg = (
        f"【用户精简画像】\n{text}\n"
        "（需要完整画像与记忆检索等完整助理能力时，请调用 personal-assistant 的 assistant skill）"
    )
    return {"injectSteps": [{"systemMessage": {"systemMessage": msg}}]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal_assistant.hooks")
    parser.add_argument("event", choices=["stop", "session-start"])
    parser.add_argument("--pa-home", default=None)
    args = parser.parse_args(argv)

    pa_home = _resolve_pa_home(args.pa_home)
    out: dict = {}
    try:
        if args.event == "stop":
            out = _handle_stop(pa_home)
        elif args.event == "session-start":
            out = _handle_session_start(pa_home)
    except Exception:  # noqa: BLE001
        out = {}

    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
