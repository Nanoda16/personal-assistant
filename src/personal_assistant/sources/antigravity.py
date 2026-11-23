"""Antigravity transcript.jsonl 解析器，支持剥离系统标签、跳过子代理与增量读取。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from personal_assistant.sources.base import RawConversation, Turn

_USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def extract_user_request(content: str) -> str:
    """提取 <USER_REQUEST>...</USER_REQUEST> 中的真实用户输入，剥离附加元数据。"""
    if not content:
        return ""
    match = _USER_REQ_RE.search(content)
    if match:
        return match.group(1).strip()
    # 若不存在标签，剥离常见的 <ADDITIONAL_METADATA> 之后的内容
    idx = content.find("<ADDITIONAL_METADATA>")
    if idx != -1:
        return content[:idx].strip()
    return content.strip()


def inspect_antigravity_transcript(path: Path) -> tuple[bool, int, str]:
    """快速扫描 transcript.jsonl，返回 (是否含USER_EXPLICIT, 最大step_index, 标题)。"""
    has_explicit = False
    max_step = 0
    title = ""
    if not path.is_file():
        return False, 0, ""

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            step_idx = int(obj.get("step_index", 0) or 0)
            if step_idx > max_step:
                max_step = step_idx
            if obj.get("source") == "USER_EXPLICIT":
                has_explicit = True
            if not title and obj.get("type") == "USER_INPUT":
                req = extract_user_request(str(obj.get("content", "")))
                if req:
                    title = " ".join(req.splitlines()).strip()[:60]
    return has_explicit, max_step, title or path.parent.parent.parent.name


def parse_antigravity_transcript(
    path: Path,
    *,
    ref: str | None = None,
    since_step: int = 0,
) -> RawConversation | None:
    """解析单个 Antigravity transcript.jsonl；若无 USER_EXPLICIT（子代理会话）则返回 None。"""
    if not path.is_file():
        return None

    has_explicit = False
    max_step = since_step
    started_at = ""
    title = ""
    turns: list[Turn] = []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("source") == "USER_EXPLICIT":
                has_explicit = True

            step_idx = int(obj.get("step_index", 0) or 0)
            if step_idx > max_step:
                max_step = step_idx

            ts = str(obj.get("created_at", "") or "")
            if not started_at and ts:
                started_at = ts

            step_type = obj.get("type")
            raw_content = str(obj.get("content", "") or "")

            if step_type == "USER_INPUT":
                text = extract_user_request(raw_content)
                if not title and text:
                    title = " ".join(text.splitlines()).strip()[:60]
                if step_idx > since_step or since_step == 0:
                    if text:
                        turns.append(Turn(role="user", text=text, ts=ts, step_index=step_idx))
            elif step_type == "PLANNER_RESPONSE":
                # 仅保留可见回复，丢弃 thinking 和工具调用内部输出
                text = raw_content.strip()
                if (step_idx > since_step or since_step == 0) and text:
                    turns.append(Turn(role="assistant", text=text, ts=ts, step_index=step_idx))

    if not has_explicit:
        return None

    conv_id = ref or path.parent.parent.parent.name
    return RawConversation(
        source="antigravity",
        ref=conv_id,
        title=title or conv_id,
        started_at=started_at,
        turns=turns,
        max_step=max_step,
    )


def discover_antigravity_transcripts(brain_dir: Path) -> list[tuple[str, Path]]:
    """发现 brain_dir 下所有会话的 transcript.jsonl，返回 [(conversation_id, path)]。"""
    if not brain_dir.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for child in sorted(brain_dir.iterdir()):
        if not child.is_dir():
            continue
        t_path = child / ".system_generated" / "logs" / "transcript.jsonl"
        if t_path.is_file():
            found.append((child.name, t_path))
    return found


class AntigravityAdapter:
    """Antigravity 数据源适配器实现。"""

    source_name = "antigravity"

    def load(self, path: Path, since_step: int = 0) -> RawConversation | None:
        return parse_antigravity_transcript(path, since_step=since_step)
