"""混合检索引擎：向量相似度 + FTS5 BM25 + 重要度 + 时间衰减。"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from personal_assistant.config import RetrievalConfig
from personal_assistant.embedder import Embedder
from personal_assistant.store import MemoryStore, utc_now_iso


def _days_since(iso_str: str, now: datetime) -> float:
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (now - dt).total_seconds() / 86400.0
        return max(0.0, delta)
    except Exception:  # noqa: BLE001
        return 0.0


def search_memories(
    store: MemoryStore,
    query: str,
    *,
    embedder: Embedder | None = None,
    cfg: RetrievalConfig | None = None,
    k: int = 8,
    types: list[str] | None = None,
    include_private: bool = False,
) -> list[dict[str, Any]]:
    """执行混合检索并更新命中记忆的使用频次与最近使用时间。"""
    q = query.strip()
    if not q:
        return []

    rcfg = cfg or RetrievalConfig()

    # 1. BM25 关键词候选
    bm25_scores = store.fts_search(
        q,
        limit=max(30, k * 4),
        types=types,
        include_private=include_private,
    )

    # 2. 读取符合过滤条件的 active 记忆元数据
    where_parts = ["status = 'active'"]
    params: list[Any] = []
    if not include_private:
        where_parts.append("sensitivity != 'private'")
    if types:
        placeholders = ",".join("?" for _ in types)
        where_parts.append(f"type IN ({placeholders})")
        params.extend(types)

    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, layer, type, content, source, source_ref,
                   importance, confidence, sensitivity,
                   created_at, updated_at, last_used_at, use_count
            FROM memories
            WHERE {' AND '.join(where_parts)}
            """,
            params,
        ).fetchall()

        if not rows:
            return []

        row_map = {int(r["id"]): dict(r) for r in rows}

        # 3. 向量相似度候选（若 embedder 可用）
        vec_scores: dict[int, float] = {}
        if embedder is not None and embedder.available:
            q_vecs = embedder.embed([q])
            if q_vecs is not None and len(q_vecs) == 1:
                vec_scores = store.vector_index.search_scores(
                    conn,
                    q_vecs[0],
                    candidate_ids=list(row_map.keys()),
                )

    # 若既无向量分数又无 BM25 分数，做一次轻量 LIKE 兜底，避免短词/符号查询完全无召回
    if not vec_scores and not bm25_scores:
        q_lower = q.lower()
        for mid, item in row_map.items():
            if q_lower in item["content"].lower():
                bm25_scores[mid] = 0.6

    candidate_ids = set(bm25_scores.keys()) | set(vec_scores.keys())
    if not candidate_ids:
        return []

    now = datetime.now(timezone.utc)
    half_life = max(1.0, rcfg.recency_half_life_days)

    # 当没有向量分数时，把向量权重自动让渡给 BM25，保证纯关键词检索也能合理排序
    has_vec = bool(vec_scores)
    w_vec = rcfg.vector_weight if has_vec else 0.0
    w_bm25 = rcfg.bm25_weight if has_vec else (rcfg.bm25_weight + rcfg.vector_weight)

    scored: list[tuple[float, dict[str, Any]]] = []
    for mid in candidate_ids:
        item = row_map.get(mid)
        if item is None:
            continue
        v_s = vec_scores.get(mid, 0.0)
        b_s = bm25_scores.get(mid, 0.0)
        imp = float(item["importance"])
        days = _days_since(item["last_used_at"] or item["created_at"], now)
        recency = math.exp(-math.log(2.0) * days / half_life)

        final_score = (
            w_vec * v_s
            + w_bm25 * b_s
            + rcfg.importance_weight * imp
            + rcfg.recency_weight * recency
        )
        enriched = dict(item)
        enriched["score"] = round(final_score, 4)
        enriched["score_detail"] = {
            "vector": round(v_s, 4),
            "bm25": round(b_s, 4),
            "importance": round(imp, 4),
            "recency": round(recency, 4),
        }
        scored.append((final_score, enriched))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = [item for _, item in scored[: max(1, k)]]

    # 更新命中记忆的 use_count 与 last_used_at
    if results:
        hit_ids = [r["id"] for r in results]
        placeholders = ",".join("?" for _ in hit_ids)
        now_iso = utc_now_iso()
        with store.connect() as conn:
            conn.execute(
                f"""
                UPDATE memories
                SET use_count = use_count + 1, last_used_at = ?
                WHERE id IN ({placeholders})
                """,
                [now_iso, *hit_ids],
            )
            conn.commit()

    return results
