"""Antigravity 生命周期 Hook 入口（保持极度轻量，禁止导入重型依赖）。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
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
    _maybe_spawn_autopilot(pa_home)
    return {}


def _maybe_spawn_autopilot(pa_home: Path) -> None:
    """在 api + silent 模式下脱离当前进程拉起后台静默维护（不等待退出）。"""
    if os.environ.get("PA_AUTOPILOT_DISABLE", "").strip() == "1":
        return
    cfg_file = pa_home / "config.toml"
    if not cfg_file.is_file():
        return
    try:
        with cfg_file.open("rb") as f:
            raw = tomllib.load(f)
    except Exception:  # noqa: BLE001
        return

    extractor_mode = str((raw.get("extractor") or {}).get("mode", "agent")).strip().lower()
    silent_flag = (raw.get("behavior") or {}).get("silent", True)
    if extractor_mode != "api" or silent_flag is False:
        return

    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = f"{_SRC_ROOT}{os.pathsep}{existing_pp}" if existing_pp else str(_SRC_ROOT)

    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(pa_home),
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(
            [sys.executable, "-m", "personal_assistant.autopilot", "--pa-home", str(pa_home)],
            **kwargs,
        )
    except Exception:  # noqa: BLE001
        pass


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
        f"【用户画像·仅供内部理解】\n{text}\n"
        "（静默模式：据此暗中理解用户、调整回答，不要向用户复述或提及画像/记忆系统的存在，也不要汇报记忆维护过程；"
        "仅当用户主动要求查看时才展示。发现用户表述与画像不符时，静默调用 personal-assistant 的 profile_correct 修正。"
        "需要深度个性化时可静默调用 memory_search。）"
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
