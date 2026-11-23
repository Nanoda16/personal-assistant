"""用户画像引擎：版本化存储、提案 Diff、回滚、即时纠正与精简/完整渲染。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from personal_assistant.store import MemoryStore, utc_now_iso

LIST_SECTIONS = (
    "identity",
    "goals",
    "habits",
    "preferences",
    "communication",
    "interests",
    "avoid",
)


def empty_profile() -> dict[str, Any]:
    """返回初始化空画像结构。"""
    return {
        "identity": [],
        "goals": [],
        "skills": {},
        "habits": [],
        "preferences": [],
        "communication": [],
        "interests": [],
        "avoid": [],
    }


def _load_latest_doc(store: MemoryStore) -> tuple[int, dict[str, Any]]:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT version, doc_json FROM profile_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return 0, empty_profile()
    return int(row["version"]), json.loads(row["doc_json"])


def _write_compact_cache(compact_path: Path | None, text: str) -> None:
    if compact_path is None:
        return
    compact_path.parent.mkdir(parents=True, exist_ok=True)
    compact_path.write_text(text, encoding="utf-8")


def _save_version(
    store: MemoryStore,
    doc: dict[str, Any],
    *,
    note: str,
    origin: str,
    compact_path: Path | None = None,
    compact_max_chars: int = 600,
) -> int:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS ver FROM profile_versions"
        ).fetchone()
        next_ver = int(row["ver"]) + 1
        conn.execute(
            """
            INSERT INTO profile_versions(version, created_at, doc_json, note, origin)
            VALUES (?, ?, ?, ?, ?)
            """,
            (next_ver, utc_now_iso(), json.dumps(doc, ensure_ascii=False), note, origin),
        )
        conn.commit()
    _write_compact_cache(compact_path, render_compact(doc, max_chars=compact_max_chars))
    return next_ver


def render_compact(doc: dict[str, Any], max_chars: int = 600) -> str:
    """按优先级渲染精简画像（identity > goals > communication > 前5高置信技能），严格限制字符数。"""
    parts: list[str] = []

    def _fmt_list(label: str, items: list[Any], limit: int = 3) -> str | None:
        texts = [str(it.get("text", "")).strip() for it in items if isinstance(it, dict) and it.get("text")]
        if not texts:
            return None
        return f"- {label}：{'；'.join(texts[:limit])}"

    for key, label in (("identity", "身份"), ("goals", "目标"), ("communication", "沟通")):
        line = _fmt_list(label, doc.get(key) or [])
        if line:
            parts.append(line)

    skills = doc.get("skills") or {}
    if isinstance(skills, dict) and skills:
        qualified = [
            (name, info)
            for name, info in skills.items()
            if isinstance(info, dict) and float(info.get("confidence", 0.0)) >= 0.6
        ]
        qualified.sort(
            key=lambda kv: (float(kv[1].get("level", 0)), float(kv[1].get("confidence", 0))),
            reverse=True,
        )
        if qualified:
            top_str = "、".join(f"{name}({info.get('level', 0)}/5)" for name, info in qualified[:5])
            parts.append(f"- 核心技能：{top_str}")

    if not parts:
        return ""

    out = "\n".join(parts)
    if len(out) > max_chars:
        out = out[: max(0, max_chars - 1)].rstrip() + "…"
    return out


def render_full(doc: dict[str, Any], version: int = 0) -> str:
    """渲染完整 Markdown 用户画像。"""
    header = f"# 用户画像（v{version}）" if version > 0 else "# 用户画像（尚未初始化版本）"
    lines: list[str] = [header]

    labels = {
        "identity": "基本信息与身份",
        "goals": "当前目标与阶段",
        "habits": "工作与生活习惯",
        "preferences": "偏好",
        "communication": "沟通风格",
        "interests": "兴趣方向",
        "avoid": "雷区与禁忌",
    }
    for key, title in labels.items():
        items = doc.get(key) or []
        lines.append(f"\n## {title}")
        if not items:
            lines.append("- （暂无记录）")
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            conf = float(it.get("confidence", 0.7))
            lines.append(f"- {it.get('text', '')}（置信度 {conf:.2f}）")

    lines.append("\n## 技能矩阵")
    skills = doc.get("skills") or {}
    if not skills:
        lines.append("- （暂无记录）")
    else:
        for name, info in sorted(skills.items()):
            if not isinstance(info, dict):
                continue
            lvl = info.get("level", 0)
            conf = float(info.get("confidence", 0.7))
            ev = info.get("evidence", "")
            ev_suffix = f" — {ev}" if ev else ""
            lines.append(f"- **{name}**：{lvl}/5（置信度 {conf:.2f}）{ev_suffix}")

    return "\n".join(lines)


def get_profile(
    store: MemoryStore,
    *,
    mode: str = "compact",
    max_chars: int = 600,
) -> dict[str, Any]:
    """获取指定模式的画像文本与结构。"""
    ver, doc = _load_latest_doc(store)
    text = render_full(doc, version=ver) if mode == "full" else render_compact(doc, max_chars=max_chars)
    return {"version": ver, "mode": mode, "text": text, "doc": doc}


def _apply_patch_ops(doc: dict[str, Any], patch: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    new_doc = copy.deepcopy(doc)
    diffs: list[str] = []
    now = utc_now_iso()

    for op_item in patch:
        if not isinstance(op_item, dict):
            continue
        op = str(op_item.get("op", "add")).lower()
        section = str(op_item.get("section", "")).strip()
        if section == "skills" or section.startswith("skills."):
            skill_name = (
                section.split(".", 1)[1].strip()
                if "." in section
                else str(op_item.get("key") or op_item.get("name") or "").strip()
            )
            if not skill_name:
                continue
            skills = new_doc.setdefault("skills", {})
            if op == "remove":
                if skill_name in skills:
                    del skills[skill_name]
                    diffs.append(f"- [skills.{skill_name}] 移除")
            else:
                val = op_item.get("value") if isinstance(op_item.get("value"), dict) else op_item
                old_val = skills.get(skill_name)
                skills[skill_name] = {
                    "level": int(val.get("level", old_val.get("level", 2) if old_val else 2)),
                    "confidence": float(val.get("confidence", 0.75)),
                    "evidence": str(val.get("evidence", old_val.get("evidence", "") if old_val else "")),
                    "updated_at": now,
                }
                tag = "~" if old_val else "+"
                diffs.append(
                    f"{tag} [skills.{skill_name}] level={skills[skill_name]['level']}/5 ({skills[skill_name]['evidence']})"
                )
            continue

        if section not in LIST_SECTIONS:
            continue
        lst = new_doc.setdefault(section, [])
        val = op_item.get("value")
        text = str(val.get("text", "") if isinstance(val, dict) else (val or op_item.get("text", ""))).strip()
        if not text:
            continue

        if op == "remove":
            before = len(lst)
            lst[:] = [it for it in lst if it.get("text") != text]
            if len(lst) < before:
                diffs.append(f"- [{section}] {text}")
        elif op == "update":
            match_text = str(op_item.get("match", text)).strip()
            conf = float(val.get("confidence", 0.8) if isinstance(val, dict) else op_item.get("confidence", 0.8))
            ev_ids = list(val.get("evidence", []) if isinstance(val, dict) else op_item.get("evidence", []))
            replaced = False
            for it in lst:
                if it.get("text") == match_text:
                    it["text"] = text
                    it["confidence"] = conf
                    it["evidence"] = ev_ids
                    it["updated_at"] = now
                    replaced = True
                    break
            if not replaced:
                lst.append({"text": text, "confidence": conf, "evidence": ev_ids, "updated_at": now})
            diffs.append(f"~ [{section}] {text}")
        else:
            # add：避免重复文本
            if not any(it.get("text") == text for it in lst):
                conf = float(val.get("confidence", 0.75) if isinstance(val, dict) else op_item.get("confidence", 0.75))
                ev_ids = list(val.get("evidence", []) if isinstance(val, dict) else op_item.get("evidence", []))
                lst.append({"text": text, "confidence": conf, "evidence": ev_ids, "updated_at": now})
                diffs.append(f"+ [{section}] {text}")

    return new_doc, diffs


def propose(store: MemoryStore, patch: list[dict[str, Any]], note: str = "") -> dict[str, Any]:
    """生成画像变更提案与可读 diff，等待用户确认后 apply。"""
    _, doc = _load_latest_doc(store)
    _, diffs = _apply_patch_ops(doc, patch)
    diff_text = "\n".join(diffs) if diffs else "（无实际字段变更）"

    with store.connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO profile_proposals(created_at, patch_json, diff_text, note, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (utc_now_iso(), json.dumps(patch, ensure_ascii=False), diff_text, note),
        )
        conn.commit()
        pid = int(cur.lastrowid)

    return {"proposal_id": pid, "status": "pending", "diff": diff_text, "note": note}


def apply_proposal(
    store: MemoryStore,
    proposal_id: int,
    *,
    compact_path: Path | None = None,
    compact_max_chars: int = 600,
) -> dict[str, Any]:
    """应用已生成的画像提案并产生新版本。"""
    with store.connect() as conn:
        row = conn.execute(
            "SELECT id, patch_json, note, status FROM profile_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": f"proposal {proposal_id} not found"}
    if row["status"] == "applied":
        return {"ok": False, "error": f"proposal {proposal_id} already applied"}

    _, doc = _load_latest_doc(store)
    patch = json.loads(row["patch_json"])
    new_doc, diffs = _apply_patch_ops(doc, patch)
    new_ver = _save_version(
        store,
        new_doc,
        note=row["note"] or f"apply proposal #{proposal_id}",
        origin="proposal",
        compact_path=compact_path,
        compact_max_chars=compact_max_chars,
    )

    with store.connect() as conn:
        conn.execute("UPDATE profile_proposals SET status = 'applied' WHERE id = ?", (proposal_id,))
        conn.commit()

    return {"ok": True, "version": new_ver, "applied_changes": diffs}


def rollback(
    store: MemoryStore,
    version: int,
    *,
    compact_path: Path | None = None,
    compact_max_chars: int = 600,
) -> dict[str, Any]:
    """回滚到指定历史版本（以追加新版本的方式保留审计链）。"""
    with store.connect() as conn:
        row = conn.execute(
            "SELECT doc_json FROM profile_versions WHERE version = ?",
            (version,),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": f"version {version} not found"}

    doc = json.loads(row["doc_json"])
    new_ver = _save_version(
        store,
        doc,
        note=f"rollback to v{version}",
        origin="rollback",
        compact_path=compact_path,
        compact_max_chars=compact_max_chars,
    )
    return {"ok": True, "version": new_ver, "restored_from": version}


def correct(
    store: MemoryStore,
    statement: str,
    *,
    path: str | None = None,
    replace: str | None = None,
    compact_path: Path | None = None,
    compact_max_chars: int = 600,
) -> dict[str, Any]:
    """用户主动纠正画像：高置信度(0.95)立即生效，并记入 feedback(corrected)。"""
    text = statement.strip()
    if not text:
        return {"ok": False, "error": "empty statement"}

    section = (path or "preferences").strip()
    if section not in LIST_SECTIONS and not section.startswith("skills."):
        section = "preferences"

    if section.startswith("skills."):
        patch = [
            {
                "op": "update",
                "section": section,
                "value": {"level": 3, "confidence": 0.95, "evidence": text},
            }
        ]
    elif replace and replace.strip():
        patch = [
            {
                "op": "update",
                "section": section,
                "match": replace.strip(),
                "value": {"text": text, "confidence": 0.95},
            }
        ]
    else:
        patch = [{"op": "add", "section": section, "value": {"text": text, "confidence": 0.95}}]

    _, doc = _load_latest_doc(store)
    new_doc, diffs = _apply_patch_ops(doc, patch)
    new_ver = _save_version(
        store,
        new_doc,
        note=f"user correction: {text[:60]}",
        origin="correction",
        compact_path=compact_path,
        compact_max_chars=compact_max_chars,
    )
    store.log_feedback("corrected", f"[{section}] {text}")
    return {"ok": True, "version": new_ver, "changes": diffs}
