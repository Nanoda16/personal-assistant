"""会话扫描、增量抽取与外部数据导入管道。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_assistant.config import Config
from personal_assistant.embedder import Embedder
from personal_assistant.extractor import EXTRACTION_PROMPT, ApiExtractor, parse_json_response
from personal_assistant.sources import (
    RawConversation,
    discover_antigravity_transcripts,
    find_gemini_takeout_file,
    parse_antigravity_transcript,
    parse_chatgpt_export,
    parse_gemini_takeout,
    parse_text_file,
)
from personal_assistant.sources.antigravity import inspect_antigravity_transcript
from personal_assistant.store import MemoryStore, utc_now_iso


def _read_hook_queue(queue_path: Path) -> list[tuple[str, Path]]:
    """读取 hooks 追加的待处理会话队列。"""
    if not queue_path.is_file():
        return []
    items: list[tuple[str, Path]] = []
    seen: set[str] = set()
    with queue_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = str(obj.get("conversationId", "")).strip()
            tpath = str(obj.get("transcriptPath", "")).strip()
            if cid and tpath and cid not in seen:
                p = Path(tpath)
                if p.is_file():
                    seen.add(cid)
                    items.append((cid, p))
    return items


def scan(store: MemoryStore, cfg: Config) -> dict[str, Any]:
    """扫描 Antigravity 会话目录与 Hook 队列，更新 ingest_log。"""
    candidates: dict[str, Path] = {}
    for cid, tpath in discover_antigravity_transcripts(cfg.antigravity_brain_path):
        candidates[cid] = tpath
    for cid, tpath in _read_hook_queue(cfg.queue_path):
        candidates.setdefault(cid, tpath)

    skipped_subagent = 0
    new_or_updated = 0
    now = utc_now_iso()

    with store.connect() as conn:
        for cid, tpath in candidates.items():
            has_explicit, max_step, _ = inspect_antigravity_transcript(tpath)
            existing = conn.execute(
                "SELECT id, status, processed_step FROM ingest_log WHERE source = 'antigravity' AND ref = ?",
                (cid,),
            ).fetchone()

            if not has_explicit:
                skipped_subagent += 1
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO ingest_log(source, ref, path, status, processed_step, updated_at)
                        VALUES ('antigravity', ?, ?, 'skipped', ?, ?)
                        """,
                        (cid, str(tpath), max_step, now),
                    )
                continue

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO ingest_log(source, ref, path, status, processed_step, updated_at)
                    VALUES ('antigravity', ?, ?, 'pending', 0, ?)
                    """,
                    (cid, str(tpath), now),
                )
                new_or_updated += 1
            elif max_step > int(existing["processed_step"]) and existing["status"] == "done":
                conn.execute(
                    """
                    UPDATE ingest_log SET status = 'pending', path = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(tpath), now, existing["id"]),
                )
                new_or_updated += 1

        conn.commit()
        pending_cnt = conn.execute(
            "SELECT COUNT(*) FROM ingest_log WHERE status = 'pending'"
        ).fetchone()[0]

    return {
        "scanned": len(candidates),
        "pending": int(pending_cnt),
        "skipped_subagent": skipped_subagent,
        "new_or_updated": new_or_updated,
    }


def format_conversation_text(conv: RawConversation, max_chars: int = 12000) -> str:
    """将标准化会话格式化为紧凑文本：用户发言完整，助手发言截断至 400 字。"""
    lines: list[str] = [f"标题：{conv.title}", f"来源：{conv.source} ({conv.ref})"]
    if conv.started_at:
        lines.append(f"时间：{conv.started_at}")
    lines.append("---")

    for turn in conv.turns:
        text = turn.text.strip()
        if not text:
            continue
        if turn.role == "assistant" and len(text) > 400:
            text = text[:400] + "…（已截断）"
        label = {"user": "用户", "assistant": "助手", "note": "笔记"}.get(turn.role, turn.role)
        lines.append(f"[{label}] {text}")

    joined = "\n".join(lines)
    if len(joined) > max_chars:
        # 优先保留靠后的最新对话内容
        joined = "…（前文已截断）\n" + joined[-(max_chars - 16) :]
    return joined


def _load_conv_for_row(row: Any) -> RawConversation | None:
    source = row["source"]
    ref = row["ref"]
    path_str = row["path"]
    since_step = int(row["processed_step"])

    if source == "antigravity":
        return parse_antigravity_transcript(Path(path_str), ref=ref, since_step=since_step)
    if source == "chatgpt":
        file_part, _, idx_part = path_str.partition("#")
        convs = parse_chatgpt_export(Path(file_part))
        for c in convs:
            if c.ref == ref:
                return c
        if idx_part.isdigit() and int(idx_part) < len(convs):
            return convs[int(idx_part)]
        return None
    if source == "gemini":
        file_part, _, idx_part = path_str.partition("#")
        convs = parse_gemini_takeout(Path(file_part))
        for c in convs:
            if c.ref == ref:
                return c
        if idx_part.isdigit() and int(idx_part) < len(convs):
            return convs[int(idx_part)]
        return None
    if source in {"text", "wechat", "notes"}:
        convs = parse_text_file(Path(path_str), source=source)
        return convs[0] if convs else None
    return None


def next_item(store: MemoryStore, max_chars: int = 12000) -> dict[str, Any] | None:
    """取出下一条 pending 会话供模型或 API 抽取；若无有效增量则自动标记为 done。"""
    while True:
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT id, source, ref, path, processed_step
                FROM ingest_log
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None

        conv = _load_conv_for_row(row)
        if conv is None or not conv.turns:
            with store.connect() as conn:
                conn.execute(
                    "UPDATE ingest_log SET status = 'done', updated_at = ? WHERE id = ?",
                    (utc_now_iso(), row["id"]),
                )
                conn.commit()
            continue

        return {
            "ingest_id": int(row["id"]),
            "source": conv.source,
            "ref": conv.ref,
            "title": conv.title,
            "started_at": conv.started_at,
            "max_step": conv.max_step,
            "prompt": EXTRACTION_PROMPT,
            "text": format_conversation_text(conv, max_chars=max_chars),
        }


def commit(
    store: MemoryStore,
    ingest_id: int,
    result: dict[str, Any] | str,
    *,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """将抽取结果（episode + memories）写入存储，并更新 ingest_log 状态。"""
    data = parse_json_response(result) if isinstance(result, str) else result
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id, source, ref, path, processed_step FROM ingest_log WHERE id = ?",
            (ingest_id,),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": f"ingest_id {ingest_id} not found"}

    conv = _load_conv_for_row(row)
    max_step = conv.max_step if conv is not None else int(row["processed_step"])
    started_at = conv.started_at if conv is not None else ""

    ep = data.get("episode") or {}
    ep_title = str(ep.get("title") or (conv.title if conv else row["ref"]))
    ep_summary = str(ep.get("summary") or "")
    ep_outcome = str(ep.get("outcome") or "completed")

    episode_id: int | None = None
    if ep_summary.strip():
        episode_id = store.record_episode(
            source=row["source"],
            source_ref=row["ref"],
            title=ep_title,
            summary=ep_summary,
            outcome=ep_outcome,
            started_at=started_at,
        )

    inserted = 0
    merged = 0
    for item in data.get("memories") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        res = store.add_memory(
            content=content,
            mem_type=str(item.get("type", "fact")),
            layer="semantic",
            source=row["source"],
            source_ref=row["ref"],
            importance=float(item.get("importance", 0.5)),
            confidence=float(item.get("confidence", 0.7)),
            sensitivity=str(item.get("sensitivity", "public")),
            embedder=embedder,
            profile_paths=list(item.get("profile_paths") or []),
        )
        if res.get("action") == "inserted":
            inserted += 1
        elif res.get("action") == "merged":
            merged += 1

    with store.connect() as conn:
        conn.execute(
            """
            UPDATE ingest_log
            SET status = 'done', processed_step = ?, updated_at = ?
            WHERE id = ?
            """,
            (max_step, utc_now_iso(), ingest_id),
        )
        conn.commit()

    return {
        "ok": True,
        "ingest_id": ingest_id,
        "episode_id": episode_id,
        "memories_inserted": inserted,
        "memories_merged": merged,
    }


def run_api(
    store: MemoryStore,
    cfg: Config,
    *,
    embedder: Embedder | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """在 api 模式下自动批量抽取最多 limit 条 pending 会话。"""
    if cfg.extractor.mode != "api":
        return {
            "ok": False,
            "error": "当前 extractor.mode 为 'agent'，请使用 ingest_next + ingest_commit，或在 config.toml 启用 api 模式。",
        }

    extractor = ApiExtractor(cfg.api)
    processed: list[dict[str, Any]] = []
    for _ in range(max(1, limit)):
        item = next_item(store)
        if item is None:
            break
        result = extractor.extract(item["text"])
        committed = commit(store, item["ingest_id"], result, embedder=embedder)
        processed.append(committed)

    return {"ok": True, "processed_count": len(processed), "items": processed}


def ingest_file(
    store: MemoryStore,
    cfg: Config,
    path: Path,
    *,
    kind: str = "auto",
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    """导入外部文件/目录（ChatGPT conversations.json 或 txt/md 文本）。"""
    target = Path(path).expanduser()
    if not target.exists():
        return {"ok": False, "error": f"路径不存在: {target}"}

    resolved_kind = kind
    if resolved_kind == "auto":
        gemini_file = find_gemini_takeout_file(target)
        if gemini_file is not None:
            resolved_kind = "gemini"
            target = gemini_file
        elif target.is_file() and target.suffix.lower() == ".json":
            resolved_kind = "chatgpt"
        else:
            resolved_kind = "text"

    convs: list[RawConversation]
    if resolved_kind == "chatgpt":
        convs = parse_chatgpt_export(target)
    elif resolved_kind == "gemini":
        if target.is_dir():
            found = find_gemini_takeout_file(target)
            if found is not None:
                target = found
        convs = parse_gemini_takeout(target)
    else:
        convs = parse_text_file(target, source=resolved_kind)

    if not convs:
        return {"ok": True, "queued": 0, "message": "未解析出有效对话或文本内容"}

    now = utc_now_iso()
    queued_ids: list[int] = []
    with store.connect() as conn:
        for idx, conv in enumerate(convs):
            path_ref = f"{target.resolve()}#{idx}" if resolved_kind in {"chatgpt", "gemini"} else conv.ref
            existing = conn.execute(
                "SELECT id FROM ingest_log WHERE source = ? AND ref = ?",
                (conv.source, conv.ref),
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    """
                    INSERT INTO ingest_log(source, ref, path, status, processed_step, updated_at)
                    VALUES (?, ?, ?, 'pending', 0, ?)
                    """,
                    (conv.source, conv.ref, path_ref, now),
                )
                queued_ids.append(int(cur.lastrowid))
        conn.commit()

    if cfg.extractor.mode == "api" and queued_ids:
        api_res = run_api(store, cfg, embedder=embedder, limit=len(queued_ids))
        return {"ok": True, "queued": len(queued_ids), "api_result": api_res}

    return {
        "ok": True,
        "queued": len(queued_ids),
        "mode": cfg.extractor.mode,
        "next_step": "请调用 ingest_next + ingest_commit 完成抽取",
    }
