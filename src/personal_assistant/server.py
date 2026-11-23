"""FastMCP 服务端：暴露记忆、画像、导入与反思工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from personal_assistant import ingest, profile, reflect, retrieval
from personal_assistant.config import Config, load_config
from personal_assistant.embedder import Embedder, get_embedder
from personal_assistant.store import MemoryStore

mcp = FastMCP("personal-assistant")

_CTX: tuple[Config, MemoryStore, Embedder] | None = None


def _get_ctx() -> tuple[Config, MemoryStore, Embedder]:
    """懒加载配置、存储与嵌入器，避免 MCP Server 启动超时。"""
    global _CTX
    if _CTX is None:
        cfg = load_config()
        store = MemoryStore(cfg.db_path, redact_enabled=cfg.privacy.redact)
        embedder = get_embedder(
            enabled=cfg.embedding.enabled,
            model_name=cfg.embedding.model,
        )
        _CTX = (cfg, store, embedder)
    return _CTX


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def memory_search(
    query: str,
    k: int = 8,
    types: list[str] | None = None,
    include_private: bool = False,
) -> str:
    """混合检索长期记忆（向量相似度 + FTS5 BM25 + 重要度 + 时间衰减）。"""
    cfg, store, embedder = _get_ctx()
    items = retrieval.search_memories(
        store,
        query,
        embedder=embedder,
        cfg=cfg.retrieval,
        k=k,
        types=types,
        include_private=include_private,
    )
    return _dump({"count": len(items), "items": items})


@mcp.tool()
def memory_add(items: list[dict[str, Any]]) -> str:
    """批量写入长期记忆（自动脱敏与相似去重）。"""
    _, store, embedder = _get_ctx()
    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        res = store.add_memory(
            content=str(item.get("content", "")),
            mem_type=str(item.get("type", "fact")),
            layer=str(item.get("layer", "semantic")),
            source=str(item.get("source", "assistant")),
            source_ref=str(item.get("source_ref", "")),
            importance=float(item.get("importance", 0.6)),
            confidence=float(item.get("confidence", 0.75)),
            sensitivity=str(item.get("sensitivity", "public")),
            embedder=embedder,
            profile_paths=list(item.get("profile_paths") or []),
        )
        results.append(res)
    return _dump({"results": results})


@mcp.tool()
def memory_update(
    id: int,
    content: str | None = None,
    importance: float | None = None,
    confidence: float | None = None,
    sensitivity: str | None = None,
    status: str | None = None,
) -> str:
    """更新指定 ID 的记忆内容或属性。"""
    _, store, embedder = _get_ctx()
    res = store.update_memory(
        id,
        content=content,
        importance=importance,
        confidence=confidence,
        sensitivity=sensitivity,
        status=status,
        embedder=embedder,
    )
    return _dump(res)


@mcp.tool()
def memory_forget(query_or_id: str, dry_run: bool = True) -> str:
    """遗忘指定 ID 或关键词匹配的记忆；默认 dry_run=True 先预览。"""
    _, store, _ = _get_ctx()
    return _dump(store.forget(query_or_id, dry_run=dry_run))


@mcp.tool()
def profile_get(mode: str = "compact") -> str:
    """获取用户画像（mode='compact' 或 'full'）。"""
    cfg, store, _ = _get_ctx()
    return _dump(
        profile.get_profile(
            store,
            mode="full" if mode == "full" else "compact",
            max_chars=cfg.profile.compact_max_chars,
        )
    )


@mcp.tool()
def profile_propose(patch: list[dict[str, Any]], note: str = "") -> str:
    """提交画像更新提案并生成可读 diff（需用户确认后再 apply）。"""
    _, store, _ = _get_ctx()
    return _dump(profile.propose(store, patch=patch, note=note))


@mcp.tool()
def profile_apply(proposal_id: int) -> str:
    """应用画像更新提案并刷新精简画像缓存。"""
    cfg, store, _ = _get_ctx()
    return _dump(
        profile.apply_proposal(
            store,
            proposal_id,
            compact_path=cfg.compact_profile_path,
            compact_max_chars=cfg.profile.compact_max_chars,
        )
    )


@mcp.tool()
def profile_rollback(version: int) -> str:
    """将用户画像回滚到指定历史版本号。"""
    cfg, store, _ = _get_ctx()
    return _dump(
        profile.rollback(
            store,
            version,
            compact_path=cfg.compact_profile_path,
            compact_max_chars=cfg.profile.compact_max_chars,
        )
    )


@mcp.tool()
def profile_correct(statement: str, path: str | None = None) -> str:
    """记录用户的直接纠正并立即高置信度更新画像。"""
    cfg, store, _ = _get_ctx()
    return _dump(
        profile.correct(
            store,
            statement,
            path=path,
            compact_path=cfg.compact_profile_path,
            compact_max_chars=cfg.profile.compact_max_chars,
        )
    )


@mcp.tool()
def ingest_scan() -> str:
    """扫描 Antigravity 会话日志与 Hook 队列，登记待抽取任务。"""
    cfg, store, _ = _get_ctx()
    return _dump(ingest.scan(store, cfg))


@mcp.tool()
def ingest_next() -> str:
    """获取下一条待抽取会话文本与抽取提示词（仅 agent 模式）。"""
    cfg, store, _ = _get_ctx()
    if cfg.extractor.mode == "api":
        return _dump(
            {
                "mode": "api",
                "message": "当前已配置为 api 模式，请直接调用 ingest_run() 自动抽取。",
            }
        )
    item = ingest.next_item(store)
    if item is None:
        return _dump({"pending": False, "message": "暂无待抽取会话"})
    return _dump({"pending": True, **item})


@mcp.tool()
def ingest_commit(ingest_id: int, result: dict[str, Any]) -> str:
    """提交模型抽取出的 episode 与 memories JSON 结果并入库。"""
    _, store, embedder = _get_ctx()
    return _dump(ingest.commit(store, ingest_id, result, embedder=embedder))


@mcp.tool()
def ingest_run(limit: int = 5) -> str:
    """在 api 模式下自动批量抽取最多 limit 条待处理会话。"""
    cfg, store, embedder = _get_ctx()
    return _dump(ingest.run_api(store, cfg, embedder=embedder, limit=limit))


@mcp.tool()
def ingest_file(path: str, kind: str = "auto") -> str:
    """导入外部数据文件或目录（ChatGPT conversations.json 或 txt/md 笔记）。"""
    cfg, store, embedder = _get_ctx()
    return _dump(ingest.ingest_file(store, cfg, Path(path), kind=kind, embedder=embedder))


@mcp.tool()
def reflect_prepare() -> str:
    """准备定期反思所需的新增记忆与当前画像，供当前会话生成提案。"""
    _, store, _ = _get_ctx()
    return _dump(reflect.prepare(store))


@mcp.tool()
def reflect_run() -> str:
    """在 api 模式下自动执行一次反思并生成待确认提案。"""
    cfg, store, _ = _get_ctx()
    return _dump(reflect.run_api(store, cfg))


@mcp.tool()
def feedback_log(kind: str, note: str = "") -> str:
    """记录建议反馈（accepted | corrected | rejected）。"""
    _, store, _ = _get_ctx()
    fid = store.log_feedback(kind, note)
    return _dump({"ok": True, "feedback_id": fid})


@mcp.tool()
def stats() -> str:
    """返回记忆库、导入队列、画像版本与模型状态统计。"""
    cfg, store, embedder = _get_ctx()
    return _dump(store.stats(embedder=embedder, extractor_mode=cfg.extractor.mode))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
