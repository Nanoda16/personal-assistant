"""SQLite 存储层、FTS5 中文分词索引、向量存储接口与写入去重。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import jieba
import numpy as np

from personal_assistant.embedder import Embedder
from personal_assistant.redact import redact_text

SCHEMA_VERSION = 1

# 避免 jieba 初始化时向 stderr 打印过多日志
jieba.setLogLevel(20)


def utc_now_iso() -> str:
    """返回 ISO 8601 UTC 时间字符串。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def tokenize_zh(text: str) -> str:
    """使用 jieba 搜索引擎模式分词，供 FTS5 索引与查询使用。"""
    if not text:
        return ""
    tokens = [tok.strip() for tok in jieba.cut_for_search(text) if tok.strip()]
    return " ".join(tokens)


def normalize_text(text: str) -> str:
    """文本规范化，用于无向量时的精确去重比对。"""
    return " ".join(text.strip().lower().split())


def build_fts_query(query: str) -> str:
    """将用户查询分词并转义为安全的 FTS5 OR 查询表达式。"""
    raw_tokens = [tok.strip() for tok in jieba.cut_for_search(query) if tok.strip()]
    # 过滤掉纯标点或过短的符号，避免 FTS5 语法错误
    safe_terms: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        cleaned = tok.replace('"', '""')
        if not cleaned or all(not ch.isalnum() for ch in cleaned):
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            safe_terms.append(f'"{cleaned}"')
    return " OR ".join(safe_terms)


class VectorIndex(Protocol):
    """向量检索抽象接口，便于后续替换为 sqlite-vec 或外部向量库。"""

    def upsert(self, conn: sqlite3.Connection, memory_id: int, vec: np.ndarray) -> None: ...

    def search_scores(
        self,
        conn: sqlite3.Connection,
        query_vec: np.ndarray,
        candidate_ids: list[int] | None = None,
    ) -> dict[int, float]: ...


class SqliteNumpyVectorIndex:
    """基于 SQLite BLOB + NumPy 余弦相似度的轻量向量索引。"""

    def upsert(self, conn: sqlite3.Connection, memory_id: int, vec: np.ndarray) -> None:
        arr = np.asarray(vec, dtype=np.float32).reshape(-1)
        conn.execute(
            """
            INSERT INTO embeddings(memory_id, vec) VALUES (?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET vec = excluded.vec
            """,
            (memory_id, arr.tobytes()),
        )

    def search_scores(
        self,
        conn: sqlite3.Connection,
        query_vec: np.ndarray,
        candidate_ids: list[int] | None = None,
    ) -> dict[int, float]:
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return {}
        q = q / q_norm

        if candidate_ids is not None:
            if not candidate_ids:
                return {}
            placeholders = ",".join("?" for _ in candidate_ids)
            rows = conn.execute(
                f"SELECT memory_id, vec FROM embeddings WHERE memory_id IN ({placeholders})",
                candidate_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.memory_id, e.vec
                FROM embeddings e
                JOIN memories m ON m.id = e.memory_id
                WHERE m.status = 'active'
                """
            ).fetchall()

        if not rows:
            return {}

        scores: dict[int, float] = {}
        ids: list[int] = []
        vecs: list[np.ndarray] = []
        dim = q.shape[0]
        for row in rows:
            arr = np.frombuffer(row["vec"], dtype=np.float32)
            if arr.shape[0] != dim:
                continue
            ids.append(int(row["memory_id"]))
            vecs.append(arr)

        if not vecs:
            return {}

        mat = np.vstack(vecs)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0.0, 1.0, norms)
        mat_normed = mat / norms
        sims = mat_normed @ q
        for mid, sim in zip(ids, sims.tolist(), strict=True):
            # 映射到 [0, 1] 区间，负值截断为 0
            scores[mid] = max(0.0, float(sim))
        return scores


_DDL_V1 = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    layer TEXT NOT NULL DEFAULT 'semantic',
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.7,
    sensitivity TEXT NOT NULL DEFAULT 'public',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    superseded_by INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_memories_status_type ON memories(status, type);
CREATE INDEX IF NOT EXISTS idx_memories_sensitivity ON memories(sensitivity);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    tokens,
    content='memories',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, tokens) VALUES (new.id, new.tokens);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, tokens) VALUES ('delete', old.id, old.tokens);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF tokens ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, tokens) VALUES ('delete', old.id, old.tokens);
    INSERT INTO memories_fts(rowid, tokens) VALUES (new.id, new.tokens);
END;

CREATE TABLE IF NOT EXISTS embeddings (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ref TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    processed_step INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(source, ref)
);

CREATE TABLE IF NOT EXISTS profile_versions (
    version INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    doc_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS profile_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    patch_json TEXT NOT NULL,
    diff_text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS evidence (
    profile_path TEXT NOT NULL,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    PRIMARY KEY (profile_path, memory_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


class MemoryStore:
    """SQLite 存储管理器，封装迁移、CRUD 与去重合并。"""

    def __init__(
        self,
        db_path: Path,
        vector_index: VectorIndex | None = None,
        redact_enabled: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.vector_index: VectorIndex = vector_index or SqliteNumpyVectorIndex()
        self.redact_enabled = redact_enabled
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            ver = int(conn.execute("PRAGMA user_version;").fetchone()[0])
            if ver < 1:
                conn.executescript(_DDL_V1)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
                conn.commit()

    def add_memory(
        self,
        *,
        content: str,
        mem_type: str = "fact",
        layer: str = "semantic",
        source: str = "manual",
        source_ref: str = "",
        importance: float = 0.5,
        confidence: float = 0.7,
        sensitivity: str = "public",
        embedder: Embedder | None = None,
        profile_paths: list[str] | None = None,
        dedup_threshold: float = 0.92,
    ) -> dict[str, Any]:
        """写入一条记忆，支持脱敏与相似记忆自动合并。"""
        clean_content = redact_text(content) if self.redact_enabled else content
        clean_content = clean_content.strip()
        if not clean_content:
            return {"action": "skipped", "reason": "empty_content"}

        importance = max(0.0, min(1.0, float(importance)))
        confidence = max(0.0, min(1.0, float(confidence)))
        sensitivity = "private" if sensitivity == "private" else "public"
        now = utc_now_iso()

        vec: np.ndarray | None = None
        if embedder is not None and embedder.available:
            encoded = embedder.embed([clean_content])
            if encoded is not None and len(encoded) == 1:
                vec = encoded[0]

        with self.connect() as conn:
            # 1. 查找同类型 active 记忆进行去重合并
            merged_id = self._find_duplicate(
                conn=conn,
                content=clean_content,
                mem_type=mem_type,
                vec=vec,
                threshold=dedup_threshold,
            )
            if merged_id is not None:
                row = conn.execute(
                    "SELECT content, importance, confidence FROM memories WHERE id = ?",
                    (merged_id,),
                ).fetchone()
                best_content = (
                    clean_content if len(clean_content) > len(row["content"]) else row["content"]
                )
                new_tokens = tokenize_zh(best_content)
                new_conf = min(1.0, max(float(row["confidence"]), confidence) + 0.1)
                new_imp = max(float(row["importance"]), importance)
                conn.execute(
                    """
                    UPDATE memories
                    SET content = ?, tokens = ?, importance = ?, confidence = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (best_content, new_tokens, new_imp, new_conf, now, merged_id),
                )
                if vec is not None and best_content == clean_content:
                    self.vector_index.upsert(conn, merged_id, vec)
                if profile_paths:
                    self._link_evidence(conn, merged_id, profile_paths)
                conn.commit()
                return {"action": "merged", "id": merged_id, "confidence": round(new_conf, 4)}

            # 2. 插入新记忆
            tokens = tokenize_zh(clean_content)
            cur = conn.execute(
                """
                INSERT INTO memories (
                    layer, type, content, tokens, source, source_ref,
                    importance, confidence, sensitivity,
                    created_at, updated_at, last_used_at, use_count, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active')
                """,
                (
                    layer,
                    mem_type,
                    clean_content,
                    tokens,
                    source,
                    source_ref,
                    importance,
                    confidence,
                    sensitivity,
                    now,
                    now,
                    now,
                ),
            )
            memory_id = int(cur.lastrowid)
            if vec is not None:
                self.vector_index.upsert(conn, memory_id, vec)
            if profile_paths:
                self._link_evidence(conn, memory_id, profile_paths)
            conn.commit()
            return {"action": "inserted", "id": memory_id}

    def _find_duplicate(
        self,
        conn: sqlite3.Connection,
        content: str,
        mem_type: str,
        vec: np.ndarray | None,
        threshold: float,
    ) -> int | None:
        rows = conn.execute(
            "SELECT id, content FROM memories WHERE status = 'active' AND type = ?",
            (mem_type,),
        ).fetchall()
        if not rows:
            return None

        if vec is not None:
            candidate_ids = [int(r["id"]) for r in rows]
            scores = self.vector_index.search_scores(conn, vec, candidate_ids=candidate_ids)
            if scores:
                best_id, best_sim = max(scores.items(), key=lambda kv: kv[1])
                if best_sim >= threshold:
                    return best_id

        # 无向量或向量未命中时退化为规范化精确匹配
        norm_new = normalize_text(content)
        for r in rows:
            if normalize_text(r["content"]) == norm_new:
                return int(r["id"])
        return None

    def _link_evidence(
        self, conn: sqlite3.Connection, memory_id: int, profile_paths: list[str]
    ) -> None:
        for path in profile_paths:
            p = path.strip()
            if p:
                conn.execute(
                    "INSERT OR IGNORE INTO evidence(profile_path, memory_id) VALUES (?, ?)",
                    (p, memory_id),
                )

    def update_memory(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
        status: str | None = None,
        embedder: Embedder | None = None,
    ) -> dict[str, Any]:
        """更新指定记忆的字段。"""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": f"memory {memory_id} not found"}

            fields: list[str] = []
            params: list[Any] = []
            new_clean: str | None = None
            if content is not None:
                new_clean = redact_text(content) if self.redact_enabled else content
                new_clean = new_clean.strip()
                fields.extend(["content = ?", "tokens = ?"])
                params.extend([new_clean, tokenize_zh(new_clean)])
            if importance is not None:
                fields.append("importance = ?")
                params.append(max(0.0, min(1.0, float(importance))))
            if confidence is not None:
                fields.append("confidence = ?")
                params.append(max(0.0, min(1.0, float(confidence))))
            if sensitivity is not None:
                fields.append("sensitivity = ?")
                params.append("private" if sensitivity == "private" else "public")
            if status is not None and status in {"active", "archived", "forgotten"}:
                fields.append("status = ?")
                params.append(status)

            if not fields:
                return {"ok": True, "id": memory_id, "updated": False}

            fields.append("updated_at = ?")
            params.append(utc_now_iso())
            params.append(memory_id)
            conn.execute(f"UPDATE memories SET {', '.join(fields)} WHERE id = ?", params)

            if new_clean is not None and embedder is not None and embedder.available:
                encoded = embedder.embed([new_clean])
                if encoded is not None and len(encoded) == 1:
                    self.vector_index.upsert(conn, memory_id, encoded[0])

            conn.commit()
            return {"ok": True, "id": memory_id, "updated": True}

    def forget(self, query_or_id: str | int, dry_run: bool = True) -> dict[str, Any]:
        """遗忘指定 ID 或匹配关键词的记忆（支持 dry_run 预览）。"""
        with self.connect() as conn:
            matches: list[dict[str, Any]] = []
            target_id: int | None = None
            if isinstance(query_or_id, int) or (
                isinstance(query_or_id, str) and query_or_id.strip().isdigit()
            ):
                target_id = int(query_or_id)

            if target_id is not None:
                row = conn.execute(
                    "SELECT id, type, content, status FROM memories WHERE id = ?",
                    (target_id,),
                ).fetchone()
                if row:
                    matches.append(dict(row))
            else:
                q = str(query_or_id).strip()
                fts_q = build_fts_query(q)
                if fts_q:
                    rows = conn.execute(
                        """
                        SELECT m.id, m.type, m.content, m.status
                        FROM memories_fts f
                        JOIN memories m ON m.id = f.rowid
                        WHERE memories_fts MATCH ? AND m.status != 'forgotten'
                        LIMIT 20
                        """,
                        (fts_q,),
                    ).fetchall()
                    matches = [dict(r) for r in rows]
                if not matches and q:
                    rows = conn.execute(
                        """
                        SELECT id, type, content, status
                        FROM memories
                        WHERE status != 'forgotten' AND content LIKE ?
                        LIMIT 20
                        """,
                        (f"%{q}%",),
                    ).fetchall()
                    matches = [dict(r) for r in rows]

            if not dry_run and matches:
                ids = [m["id"] for m in matches]
                placeholders = ",".join("?" for _ in ids)
                now = utc_now_iso()
                conn.execute(
                    f"UPDATE memories SET status = 'forgotten', updated_at = ? WHERE id IN ({placeholders})",
                    [now, *ids],
                )
                conn.commit()

            return {
                "dry_run": dry_run,
                "matched_count": len(matches),
                "matches": matches,
            }

    def fts_search(
        self,
        query: str,
        *,
        limit: int = 30,
        types: list[str] | None = None,
        include_private: bool = False,
    ) -> dict[int, float]:
        """执行 FTS5 BM25 检索，返回 {memory_id: 归一化得分[0,1]}。"""
        fts_q = build_fts_query(query)
        if not fts_q:
            return {}

        where_parts = ["memories_fts MATCH ?", "m.status = 'active'"]
        params: list[Any] = [fts_q]
        if not include_private:
            where_parts.append("m.sensitivity != 'private'")
        if types:
            placeholders = ",".join("?" for _ in types)
            where_parts.append(f"m.type IN ({placeholders})")
            params.extend(types)
        params.append(limit)

        sql = f"""
            SELECT m.id, bm25(memories_fts) AS rank
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE {' AND '.join(where_parts)}
            ORDER BY rank ASC
            LIMIT ?
        """
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        if not rows:
            return {}
        # SQLite FTS5 的 bm25() 返回负值，越小越相关；转为正数后线性归一化到 [0, 1]
        raw_scores = {int(r["id"]): max(0.0, -float(r["rank"])) for r in rows}
        max_s = max(raw_scores.values()) or 1.0
        return {mid: s / max_s for mid, s in raw_scores.items()}

    def record_episode(
        self,
        *,
        source: str,
        source_ref: str,
        title: str,
        summary: str,
        outcome: str = "",
        started_at: str = "",
    ) -> int:
        clean_title = redact_text(title) if self.redact_enabled else title
        clean_summary = redact_text(summary) if self.redact_enabled else summary
        clean_outcome = redact_text(outcome) if self.redact_enabled else outcome
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO episodes(source, source_ref, title, summary, outcome, started_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (source, source_ref, clean_title, clean_summary, clean_outcome, started_at, utc_now_iso()),
            )
            conn.commit()
            return int(cur.lastrowid)

    def log_feedback(self, kind: str, note: str = "") -> int:
        if kind not in {"accepted", "corrected", "rejected"}:
            kind = "accepted"
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO feedback(ts, kind, note) VALUES (?, ?, ?)",
                (utc_now_iso(), kind, note),
            )
            conn.commit()
            return int(cur.lastrowid)

    def stats(self, embedder: Embedder | None = None, extractor_mode: str = "agent") -> dict[str, Any]:
        """汇总当前记忆库统计信息。"""
        with self.connect() as conn:
            mem_total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE status = 'active'"
            ).fetchone()[0]
            by_type = {
                r["type"]: r["cnt"]
                for r in conn.execute(
                    "SELECT type, COUNT(*) AS cnt FROM memories WHERE status = 'active' GROUP BY type"
                ).fetchall()
            }
            ep_total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
            pending_ingest = conn.execute(
                "SELECT COUNT(*) FROM ingest_log WHERE status = 'pending'"
            ).fetchone()[0]
            done_ingest = conn.execute(
                "SELECT COUNT(*) FROM ingest_log WHERE status = 'done'"
            ).fetchone()[0]
            skipped_ingest = conn.execute(
                "SELECT COUNT(*) FROM ingest_log WHERE status = 'skipped'"
            ).fetchone()[0]
            prof_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS ver FROM profile_versions"
            ).fetchone()
            pending_proposals = conn.execute(
                "SELECT COUNT(*) FROM profile_proposals WHERE status = 'pending'"
            ).fetchone()[0]
            vec_count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

        return {
            "active_memories": int(mem_total),
            "memories_by_type": by_type,
            "episodes": int(ep_total),
            "vectors": int(vec_count),
            "ingest": {
                "pending": int(pending_ingest),
                "done": int(done_ingest),
                "skipped": int(skipped_ingest),
            },
            "profile_version": int(prof_row["ver"]),
            "pending_proposals": int(pending_proposals),
            "embedder": embedder.name if embedder is not None else "unknown",
            "extractor_mode": extractor_mode,
        }

    def export_all(self) -> dict[str, Any]:
        """导出全部核心数据（用于备份或迁移）。"""
        with self.connect() as conn:
            memories = [
                dict(r)
                for r in conn.execute("SELECT * FROM memories ORDER BY id ASC").fetchall()
            ]
            episodes = [
                dict(r)
                for r in conn.execute("SELECT * FROM episodes ORDER BY id ASC").fetchall()
            ]
            profiles = [
                dict(r)
                for r in conn.execute(
                    "SELECT version, created_at, doc_json, note, origin FROM profile_versions ORDER BY version ASC"
                ).fetchall()
            ]
        for p in profiles:
            p["doc"] = json.loads(p.pop("doc_json"))
        return {
            "exported_at": utc_now_iso(),
            "memories": memories,
            "episodes": episodes,
            "profile_versions": profiles,
        }
