from pathlib import Path

from personal_assistant.embedder import NullEmbedder
from personal_assistant.retrieval import search_memories
from personal_assistant.store import MemoryStore


def test_fts_chinese_search_and_private_isolation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    emb = NullEmbedder()

    r1 = store.add_memory(
        content="完成了 DocQA 前端 UI 重构，采用 React 19 与黑白简约风格",
        mem_type="event",
        importance=0.9,
        embedder=emb,
    )
    assert r1["action"] == "inserted"

    store.add_memory(
        content="个人私密健康备忘：最近睡眠略少",
        mem_type="fact",
        importance=0.8,
        sensitivity="private",
        embedder=emb,
    )

    hits = search_memories(store, "前端重构", embedder=emb, k=5)
    assert len(hits) >= 1
    assert "DocQA 前端 UI 重构" in hits[0]["content"]

    # 默认不返回 private 记忆
    priv_hits_hidden = search_memories(store, "睡眠", embedder=emb, k=5, include_private=False)
    assert priv_hits_hidden == []

    # 显式允许时才返回
    priv_hits_visible = search_memories(store, "睡眠", embedder=emb, k=5, include_private=True)
    assert len(priv_hits_visible) == 1


def test_dedup_merge_without_embedder(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    emb = NullEmbedder()

    r1 = store.add_memory(
        content="用户偏好使用中文回复",
        mem_type="preference",
        confidence=0.7,
        embedder=emb,
    )
    r2 = store.add_memory(
        content="  用户偏好使用中文回复  ",
        mem_type="preference",
        confidence=0.7,
        embedder=emb,
    )
    assert r1["action"] == "inserted"
    assert r2["action"] == "merged"
    assert r2["id"] == r1["id"]
    assert r2["confidence"] == 0.8


def test_forget_dry_run_and_apply(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    store.add_memory(content="临时测试条目 Alpha", mem_type="fact")

    preview = store.forget("Alpha", dry_run=True)
    assert preview["matched_count"] == 1
    assert store.stats()["active_memories"] == 1

    applied = store.forget("Alpha", dry_run=False)
    assert applied["matched_count"] == 1
    assert store.stats()["active_memories"] == 0
