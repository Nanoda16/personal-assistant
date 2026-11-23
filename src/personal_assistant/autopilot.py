"""后台静默自动维护：会话扫描、API 抽取、定期反思与画像自动应用。"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from personal_assistant import ingest, profile, reflect
from personal_assistant.config import Config, load_config
from personal_assistant.embedder import Embedder, get_embedder
from personal_assistant.store import MemoryStore, utc_now_iso

_LOCK_STALE_SECONDS = 30 * 60


def _acquire_lock(lock_path: Path) -> int | None:
    """获取文件锁；若存在未过期锁返回 None，超过 30 分钟视为残留锁并清理重试。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            return fd
        except FileExistsError:
            if attempt == 0:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age > _LOCK_STALE_SECONDS:
                        lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
            return None
        except OSError:
            return None
    return None


def _release_lock(fd: int | None, lock_path: Path) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _count_episodes_since_last_profile(store: MemoryStore) -> int:
    with store.connect() as conn:
        ver_row = conn.execute(
            "SELECT created_at FROM profile_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if ver_row is None:
            row = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE created_at > ?",
                (ver_row["created_at"],),
            ).fetchone()
    return int(row[0]) if row is not None else 0


def _append_log(log_path: Path, payload: dict[str, Any]) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": utc_now_iso(), **payload}
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_cycle(cfg: Config, store: MemoryStore, embedder: Embedder | None = None) -> dict[str, Any]:
    """执行一轮静默维护：扫描 + 抽取 + 达阈值后自动反思与应用。"""
    if cfg.extractor.mode != "api":
        return {"ok": False, "skipped": "extractor_mode_not_api"}
    if not cfg.api.resolve_api_key():
        return {"ok": False, "skipped": "missing_api_key"}

    lock_path = cfg.pa_home / "autopilot.lock"
    fd = _acquire_lock(lock_path)
    if fd is None:
        return {"ok": False, "skipped": "lock_active"}

    errors: list[str] = []
    ingested = 0
    memories_inserted = 0
    reflected = False
    applied_version: int | None = None

    try:
        try:
            ingest.scan(store, cfg)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"scan: {str(exc)[:180]}")

        try:
            run_res = ingest.run_api(
                store,
                cfg,
                embedder=embedder,
                limit=max(1, int(cfg.behavior.auto_ingest_limit)),
            )
            if run_res.get("ok"):
                items = run_res.get("items") or []
                ingested = len(items)
                memories_inserted = sum(int(it.get("memories_inserted", 0)) for it in items if isinstance(it, dict))
            else:
                errors.append(f"ingest: {str(run_res.get('error', ''))[:180]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ingest: {str(exc)[:180]}")

        try:
            ep_cnt = _count_episodes_since_last_profile(store)
            if ep_cnt >= max(1, int(cfg.behavior.reflect_min_episodes)):
                ref_res = reflect.run_api(store, cfg)
                pid = ref_res.get("proposal_id")
                if pid is not None:
                    reflected = True
                    if cfg.behavior.auto_apply:
                        app_res = profile.apply_proposal(
                            store,
                            int(pid),
                            compact_path=cfg.compact_profile_path,
                            compact_max_chars=cfg.profile.compact_max_chars,
                        )
                        if app_res.get("ok"):
                            applied_version = int(app_res["version"])
                        else:
                            errors.append(f"apply: {str(app_res.get('error', ''))[:180]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"reflect: {str(exc)[:180]}")

        summary: dict[str, Any] = {
            "ok": len(errors) == 0,
            "ingested": ingested,
            "memories_inserted": memories_inserted,
            "reflected": reflected,
            "applied_version": applied_version,
        }
        if errors:
            summary["errors"] = errors
        _append_log(cfg.pa_home / "autopilot.log", summary)
        return summary
    finally:
        _release_lock(fd, lock_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal_assistant.autopilot", description="后台静默维护入口")
    parser.add_argument("--pa-home", default=None)
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.pa_home)
        store = MemoryStore(cfg.db_path, redact_enabled=cfg.privacy.redact)
        emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
        run_cycle(cfg, store, emb)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
