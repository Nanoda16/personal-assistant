"""ChatGPT 官方导出文件（conversations.json）解析器。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personal_assistant.sources.base import RawConversation, Turn


def _ts_to_iso(ts: Any) -> str:
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(ts or "")


def _extract_parts(content: Any) -> str:
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            texts = [str(p) for p in parts if isinstance(p, str) and p.strip()]
            return "\n".join(texts).strip()
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
    elif isinstance(content, str):
        return content.strip()
    return ""


def _parse_single_conversation(conv: dict[str, Any], index: int) -> RawConversation | None:
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return None

    # 从 current_node 向上回溯到根节点，获取主分支对话顺序
    chain: list[dict[str, Any]] = []
    node_id = conv.get("current_node")
    visited: set[str] = set()
    while isinstance(node_id, str) and node_id in mapping and node_id not in visited:
        visited.add(node_id)
        node = mapping[node_id]
        chain.append(node)
        node_id = node.get("parent")
    chain.reverse()

    turns: list[Turn] = []
    for idx, node in enumerate(chain):
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue
        author = msg.get("author") or {}
        role = str(author.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        text = _extract_parts(msg.get("content"))
        if not text:
            continue
        turns.append(
            Turn(
                role=role,
                text=text,
                ts=_ts_to_iso(msg.get("create_time")),
                step_index=idx,
            )
        )

    if not turns:
        return None

    conv_id = str(conv.get("id") or f"chatgpt-{index}")
    title = str(conv.get("title") or turns[0].text[:60])
    started_at = _ts_to_iso(conv.get("create_time"))
    return RawConversation(
        source="chatgpt",
        ref=conv_id,
        title=title,
        started_at=started_at,
        turns=turns,
        max_step=len(turns),
    )


def parse_chatgpt_export(path: Path) -> list[RawConversation]:
    """解析 ChatGPT 导出的 conversations.json，返回标准会话列表。"""
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    results: list[RawConversation] = []
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            parsed = _parse_single_conversation(item, idx)
            if parsed is not None:
                results.append(parsed)
    return results
