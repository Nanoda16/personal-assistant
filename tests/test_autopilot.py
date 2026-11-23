import os
from pathlib import Path

from personal_assistant import autopilot, ingest, profile, reflect
from personal_assistant.config import load_config
from personal_assistant.store import MemoryStore


def test_autopilot_skips_in_agent_mode(tmp_path: Path) -> None:
    pa_home = tmp_path / "pa"
    pa_home.mkdir()
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=True)

    res = autopilot.run_cycle(cfg, store)
    assert res["ok"] is False
    assert res["skipped"] == "extractor_mode_not_api"


def test_autopilot_skips_when_lock_active(tmp_path: Path, monkeypatch) -> None:
    pa_home = tmp_path / "pa"
    pa_home.mkdir()
    (pa_home / "config.toml").write_text('[extractor]\nmode = "api"\n', encoding="utf-8")
    monkeypatch.setenv("PA_API_KEY", "dummy-key")
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=True)

    (pa_home / "autopilot.lock").write_text("99999", encoding="utf-8")
    res = autopilot.run_cycle(cfg, store)
    assert res["ok"] is False
    assert res["skipped"] == "lock_active"


def test_autopilot_runs_ingest_reflect_and_auto_apply(tmp_path: Path, monkeypatch) -> None:
    pa_home = tmp_path / "pa"
    pa_home.mkdir()
    (pa_home / "config.toml").write_text(
        '[extractor]\nmode = "api"\n[behavior]\nsilent = true\nauto_apply = true\nreflect_min_episodes = 1\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PA_API_KEY", "dummy-key")
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=True)
    store.record_episode(source="antigravity", source_ref="c1", title="测试", summary="测试事件")

    monkeypatch.setattr(ingest, "scan", lambda s, c: {"scanned": 0, "pending": 0})
    monkeypatch.setattr(
        ingest,
        "run_api",
        lambda s, c, embedder=None, limit=5: {
            "ok": True,
            "items": [{"ok": True, "memories_inserted": 2}],
        },
    )

    def _fake_reflect(s, c):
        return profile.propose(
            s,
            patch=[{"op": "add", "section": "goals", "value": {"text": "自动反思新增目标", "confidence": 0.9}}],
            note="auto test",
        )

    monkeypatch.setattr(reflect, "run_api", _fake_reflect)

    before_ver = profile.get_profile(store, mode="full")["version"]
    res = autopilot.run_cycle(cfg, store)
    assert res["ok"] is True
    assert res["ingested"] == 1
    assert res["memories_inserted"] == 2
    assert res["reflected"] is True
    assert res["applied_version"] == before_ver + 1

    prof = profile.get_profile(store, mode="full")
    assert prof["version"] == before_ver + 1
    assert "自动反思新增目标" in prof["text"]
    assert not (pa_home / "autopilot.lock").exists()
    assert (pa_home / "autopilot.log").is_file()


def test_profile_correct_with_replace_updates_existing(tmp_path: Path) -> None:
    pa_home = tmp_path / "pa"
    pa_home.mkdir()
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=True)

    profile.correct(store, "旧目标：只看纯后端岗位", path="goals")
    res = profile.correct(
        store,
        "新目标：专注 AI 应用工程方向",
        path="goals",
        replace="旧目标：只看纯后端岗位",
        compact_path=cfg.compact_profile_path,
    )
    assert res["ok"] is True
    full_text = profile.get_profile(store, mode="full")["text"]
    assert "旧目标：只看纯后端岗位" not in full_text
    assert "新目标：专注 AI 应用工程方向" in full_text
