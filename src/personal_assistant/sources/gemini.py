"""Google Takeout Gemini Apps 导出文件（我的活动记录.json / MyActivity.json）解析器。"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from personal_assistant.sources.base import RawConversation, Turn

_IGNORED_TITLE_PREFIXES = (
    "Cleared ",
    "Selected ",
    "Created Gemini Canvas",
)
_USER_PROMPT_PREFIX_RE = re.compile(r"^(?:Prompted\s+|提示[：:了]\s*)", re.IGNORECASE)
_SCRIPT_STYLE_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.IGNORECASE | re.DOTALL)
_BLOCK_END_RE = re.compile(r"</?(?:p|br\s*/?|div|li|pre|h[1-6])\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_GEMINI_APP_URL_RE = re.compile(r"https?://gemini\.google\.com/app/([A-Za-z0-9_-]+)")


def is_gemini_takeout_json(path: Path) -> bool:
    """判断 JSON 文件是否为 Google Takeout Gemini Apps 活动记录。"""
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, list) or not data:
        return False
    for item in data[:10]:
        if not isinstance(item, dict):
            continue
        if item.get("header") == "Gemini Apps":
            return True
        products = item.get("products")
        if isinstance(products, list) and "Gemini Apps" in products:
            return True
    return False


def find_gemini_takeout_file(path: Path) -> Path | None:
    """从文件或目录中定位 Gemini Apps 活动记录 JSON。"""
    if path.is_file():
        return path if is_gemini_takeout_json(path) else None
    if path.is_dir():
        for name in ("我的活动记录.json", "MyActivity.json"):
            candidate = path / name
            if candidate.is_file() and is_gemini_takeout_json(candidate):
                return candidate
    return None


def _html_to_text(html_str: str) -> str:
    """清洗 Takeout safeHtmlItem 中的 HTML 为纯文本。"""
    if not html_str:
        return ""
    cleaned = _SCRIPT_STYLE_RE.sub("", html_str)
    cleaned = _BLOCK_END_RE.sub("\n", cleaned)
    cleaned = _TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    text = "\n".join(lines).strip()
    return _MULTI_NEWLINE_RE.sub("\n\n", text)


def _group_key_for_item(item: dict[str, Any]) -> str:
    details = item.get("details")
    if isinstance(details, list) and details:
        first = details[0]
        if isinstance(first, dict):
            url = str(first.get("url") or "").strip()
            if url:
                m = _GEMINI_APP_URL_RE.search(url)
                if m:
                    return f"gemini:{m.group(1)}"
                digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
                return f"gemini:url:{digest}"
    ts = str(item.get("time") or "").strip()
    date_part = ts[:10] if len(ts) >= 10 else "unknown"
    return f"gemini:date:{date_part}"


def parse_gemini_takeout(path: Path) -> list[RawConversation]:
    """解析 Google Takeout Gemini Apps 活动记录，按会话聚合并按时间升序返回。"""
    target = find_gemini_takeout_file(path)
    if target is None:
        return []

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []

    grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_title = str(item.get("title") or "").strip()
        if any(raw_title.startswith(prefix) for prefix in _IGNORED_TITLE_PREFIXES):
            continue

        user_text = _USER_PROMPT_PREFIX_RE.sub("", raw_title).strip()
        html_items = item.get("safeHtmlItem")
        reply_parts: list[str] = []
        if isinstance(html_items, list):
            for part in html_items:
                if isinstance(part, dict):
                    txt = _html_to_text(str(part.get("html") or ""))
                    if txt:
                        reply_parts.append(txt)
        assistant_text = "\n\n".join(reply_parts).strip()

        if not user_text and not assistant_text:
            continue

        ts = str(item.get("time") or "").strip()
        group_key = _group_key_for_item(item)
        grouped[group_key].append((ts, user_text, assistant_text))

    conversations: list[RawConversation] = []
    for group_key, entries in grouped.items():
        entries.sort(key=lambda x: x[0])
        turns: list[Turn] = []
        step_idx = 0
        first_user_text = ""
        earliest_ts = entries[0][0] if entries else ""

        for ts, user_text, assistant_text in entries:
            if user_text:
                if not first_user_text:
                    first_user_text = user_text
                turns.append(Turn(role="user", text=user_text, ts=ts, step_index=step_idx))
                step_idx += 1
            if assistant_text:
                turns.append(Turn(role="assistant", text=assistant_text, ts=ts, step_index=step_idx))
                step_idx += 1

        if not turns:
            continue
        title = (first_user_text or turns[0].text).splitlines()[0][:60]
        conversations.append(
            RawConversation(
                source="gemini",
                ref=group_key,
                title=title,
                started_at=earliest_ts,
                turns=turns,
                max_step=len(turns),
            )
        )

    conversations.sort(key=lambda c: c.started_at)
    return conversations
