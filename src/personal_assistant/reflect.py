"""定期反思模块：汇总新记忆生成画像更新提案（不自动 apply）。"""

from __future__ import annotations

import json
from typing import Any

from personal_assistant.config import Config
from personal_assistant.extractor import ApiExtractor
from personal_assistant.profile import get_profile, propose
from personal_assistant.store import MemoryStore

REFLECT_PROMPT = """你正在执行个人画像的定期反思与迭代。
请对比【当前用户画像】与【新增长期记忆】，生成最小且准确的画像修改提案（patch）。

规则：
1. 只有多次出现或证据充分的结论才提升置信度或写入核心画像；
2. 避免重复添加已经存在于画像中的条目；
3. 只输出合法 JSON，格式如下：
{
  "note": "本次反思摘要说明",
  "patch": [
    {"op": "add|update|remove", "section": "identity|goals|habits|preferences|communication|interests|avoid|skills.<技能名>", "value": {"text": "...", "confidence": 0.8}}
  ]
}
其中 section 为 skills.<技能名> 时，value 为 {"level": 0-5, "confidence": 0.8, "evidence": "证据"}。
"""


def prepare(store: MemoryStore, limit: int = 80) -> dict[str, Any]:
    """准备反思所需的当前画像与上次版本以来的新增记忆（最多 80 条）。"""
    prof = get_profile(store, mode="full")
    with store.connect() as conn:
        last_ver_row = conn.execute(
            "SELECT created_at FROM profile_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if last_ver_row is not None:
            rows = conn.execute(
                """
                SELECT id, type, content, importance, confidence, created_at
                FROM memories
                WHERE status = 'active' AND created_at >= ?
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (last_ver_row["created_at"], limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, type, content, importance, confidence, created_at
                FROM memories
                WHERE status = 'active'
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    memories = [dict(r) for r in rows]
    return {
        "prompt": REFLECT_PROMPT,
        "profile_version": prof["version"],
        "current_profile": prof["doc"],
        "new_memories_count": len(memories),
        "memories": memories,
    }


def run_api(store: MemoryStore, cfg: Config) -> dict[str, Any]:
    """api 模式下调用大模型生成画像提案并保存（不自动 apply）。"""
    if cfg.extractor.mode != "api":
        return {
            "ok": False,
            "error": "当前 extractor.mode 为 'agent'，请使用 reflect_prepare 获取数据后由当前对话调用 profile_propose。",
        }
    prep = prepare(store)
    if prep["new_memories_count"] == 0:
        return {"ok": True, "message": "暂无新增记忆可供反思", "proposal_id": None}

    user_msg = json.dumps(
        {
            "current_profile": prep["current_profile"],
            "memories": prep["memories"],
        },
        ensure_ascii=False,
    )
    extractor = ApiExtractor(cfg.api)
    res = extractor.complete_json(REFLECT_PROMPT, user_msg)
    patch = list(res.get("patch") or [])
    note = str(res.get("note") or "periodic api reflect")
    return propose(store, patch=patch, note=note)
