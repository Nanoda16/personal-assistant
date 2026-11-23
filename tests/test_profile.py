from pathlib import Path

from personal_assistant.profile import (
    apply_proposal,
    correct,
    get_profile,
    propose,
    rollback,
)
from personal_assistant.store import MemoryStore


def test_profile_lifecycle_and_compact_limit(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    compact_cache = tmp_path / "profile_compact.md"

    patch = [
        {"op": "add", "section": "identity", "value": {"text": "信息管理与信息系统本科生", "confidence": 0.9}},
        {"op": "add", "section": "goals", "value": {"text": "准备 2027 届 AI 应用与后端校招", "confidence": 0.9}},
        {"op": "add", "section": "communication", "value": {"text": "偏好简体中文直接给结论", "confidence": 0.9}},
        {
            "op": "add",
            "section": "skills.Python",
            "value": {"level": 4, "confidence": 0.85, "evidence": "开发多个 FastAPI 与 MCP 工具"},
        },
    ]
    prop = propose(store, patch, note="初始画像")
    assert "+ [identity]" in prop["diff"]

    applied = apply_proposal(store, prop["proposal_id"], compact_path=compact_cache, compact_max_chars=600)
    assert applied["ok"] is True
    assert applied["version"] == 1
    assert compact_cache.is_file()

    compact = get_profile(store, mode="compact", max_chars=600)
    assert len(compact["text"]) <= 600
    assert "Python(4/5)" in compact["text"]

    # 直接纠正生成 v2
    corr = correct(store, "后端主力同时覆盖 Go 语言", path="goals", compact_path=compact_cache)
    assert corr["version"] == 2
    assert "Go" in get_profile(store, mode="full")["text"]

    # 回滚到 v1 生成 v3
    rb = rollback(store, 1, compact_path=compact_cache)
    assert rb["version"] == 3
    assert "Go" not in get_profile(store, mode="full")["text"]
