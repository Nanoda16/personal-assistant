"""Personal Assistant 命令行工具（pa / python -m personal_assistant.cli）。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from personal_assistant import hooks, ingest, profile, reflect, retrieval
from personal_assistant.config import Config, load_config
from personal_assistant.embedder import LocalEmbedder, get_embedder
from personal_assistant.store import MemoryStore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _build_ctx(pa_home: str | None = None) -> tuple[Config, MemoryStore]:
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=cfg.privacy.redact)
    return cfg, store


def _print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _cmd_init(args: argparse.Namespace) -> int:
    cfg = load_config(args.pa_home)
    cfg.pa_home.mkdir(parents=True, exist_ok=True)
    if not cfg.config_path.exists():
        example = _repo_root() / "config.example.toml"
        if example.is_file():
            shutil.copy2(example, cfg.config_path)
    MemoryStore(cfg.db_path, redact_enabled=cfg.privacy.redact)
    _print_json({"ok": True, "pa_home": str(cfg.pa_home), "db_path": str(cfg.db_path)})
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
    emb_ok = False
    if isinstance(emb, LocalEmbedder):
        emb_ok = emb.warmup()
    _print_json(
        {
            "python": sys.executable,
            "pa_home": str(cfg.pa_home),
            "db_exists": cfg.db_path.is_file(),
            "antigravity_brain_exists": cfg.antigravity_brain_path.is_dir(),
            "extractor_mode": cfg.extractor.mode,
            "embedder_model": emb.name,
            "embedder_available": emb_ok if isinstance(emb, LocalEmbedder) else emb.available,
            "embedder_dim": emb.dim,
            "stats": store.stats(embedder=emb, extractor_mode=cfg.extractor.mode),
        }
    )
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    _print_json(ingest.scan(store, cfg))
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    sub = args.ingest_action
    if sub == "next":
        item = ingest.next_item(store)
        _print_json({"pending": item is not None, **(item or {})})
        return 0
    if sub == "commit":
        raw = Path(args.json_file).read_text(encoding="utf-8")
        emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
        _print_json(ingest.commit(store, int(args.ingest_id), raw, embedder=emb))
        return 0
    if sub == "run":
        emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
        _print_json(ingest.run_api(store, cfg, embedder=emb, limit=args.limit))
        return 0
    if sub == "file":
        emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
        _print_json(
            ingest.ingest_file(store, cfg, Path(args.path), kind=args.kind, embedder=emb)
        )
        return 0
    return 1


def _cmd_search(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    emb = get_embedder(enabled=cfg.embedding.enabled, model_name=cfg.embedding.model)
    items = retrieval.search_memories(
        store,
        args.query,
        embedder=emb,
        cfg=cfg.retrieval,
        k=args.k,
        include_private=args.private,
    )
    _print_json({"count": len(items), "items": items})
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    mode = "full" if args.full else "compact"
    res = profile.get_profile(store, mode=mode, max_chars=cfg.profile.compact_max_chars)
    if args.json:
        _print_json(res)
    else:
        sys.stdout.write((res["text"] or "（暂无画像）") + "\n")
    return 0


def _cmd_reflect(args: argparse.Namespace) -> int:
    cfg, store = _build_ctx(args.pa_home)
    action = args.reflect_action or "prepare"
    if action == "prepare":
        _print_json(reflect.prepare(store))
        return 0
    if action == "propose":
        data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        _print_json(
            profile.propose(store, patch=list(data.get("patch") or []), note=str(data.get("note", "")))
        )
        return 0
    if action == "apply":
        _print_json(
            profile.apply_proposal(
                store,
                int(args.proposal_id),
                compact_path=cfg.compact_profile_path,
                compact_max_chars=cfg.profile.compact_max_chars,
            )
        )
        return 0
    if action == "run":
        _print_json(reflect.run_api(store, cfg))
        return 0
    return 1


def _cmd_forget(args: argparse.Namespace) -> int:
    _, store = _build_ctx(args.pa_home)
    _print_json(store.forget(args.target, dry_run=not args.apply))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    _, store = _build_ctx(args.pa_home)
    data = store.export_all()
    if args.output:
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_json({"ok": True, "output": args.output})
    else:
        _print_json(data)
    return 0


def _cmd_install_antigravity(args: argparse.Namespace) -> int:
    cfg = load_config(args.pa_home)
    cfg.pa_home.mkdir(parents=True, exist_ok=True)
    if not cfg.config_path.exists():
        example = _repo_root() / "config.example.toml"
        if example.is_file():
            shutil.copy2(example, cfg.config_path)
    MemoryStore(cfg.db_path, redact_enabled=cfg.privacy.redact)

    tmpl_dir = Path(args.template_dir) if args.template_dir else (_repo_root() / "integrations" / "antigravity" / "plugin")
    if not tmpl_dir.is_dir():
        _print_json({"ok": False, "error": f"找不到模板目录: {tmpl_dir}"})
        return 1

    target_dir = (
        Path(args.plugin_dir).expanduser().resolve()
        if args.plugin_dir
        else (Path.home() / ".gemini" / "config" / "plugins" / "personal-assistant").resolve()
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    python_exe = sys.executable
    pa_home_str = str(cfg.pa_home)
    src_path_str = str(_repo_root() / "src")

    # 复制非模板文件（plugin.json, skills/, rules/, assets/）
    for item in tmpl_dir.rglob("*"):
        rel = item.relative_to(tmpl_dir)
        if item.name.endswith(".tmpl"):
            continue
        dest = target_dir / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)

    # 使用 json.dumps 渲染 mcp_config.json 与 hooks.json，确保 Windows 反斜杠正确转义
    mcp_tmpl = (tmpl_dir / "mcp_config.json.tmpl").read_text(encoding="utf-8")
    mcp_rendered = (
        mcp_tmpl.replace("__PYTHON__", json.dumps(python_exe)[1:-1])
        .replace("__PA_HOME__", json.dumps(pa_home_str)[1:-1])
        .replace("__PYTHONPATH__", json.dumps(src_path_str)[1:-1])
    )
    (target_dir / "mcp_config.json").write_text(mcp_rendered, encoding="utf-8")

    hooks_tmpl = (tmpl_dir / "hooks.json.tmpl").read_text(encoding="utf-8")
    hooks_rendered = (
        hooks_tmpl.replace("__PYTHON__", json.dumps(python_exe)[1:-1])
        .replace("__PA_HOME__", json.dumps(pa_home_str)[1:-1])
    )
    (target_dir / "hooks.json").write_text(hooks_rendered, encoding="utf-8")

    _print_json(
        {
            "ok": True,
            "plugin_dir": str(target_dir),
            "pa_home": pa_home_str,
            "python": python_exe,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pa", description="Personal Assistant CLI")
    parser.add_argument("--pa-home", default=None, help="覆盖 PA_HOME 数据目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="初始化数据目录与数据库")
    sub.add_parser("doctor", help="检查依赖、数据库与本地向量模型")
    sub.add_parser("scan", help="扫描 Antigravity 会话日志与队列")

    p_ing = sub.add_parser("ingest", help="会话抽取与外部数据导入")
    ing_sub = p_ing.add_subparsers(dest="ingest_action", required=True)
    ing_sub.add_parser("next", help="获取下一条待抽取会话")
    p_ic = ing_sub.add_parser("commit", help="提交 JSON 抽取结果")
    p_ic.add_argument("ingest_id", type=int)
    p_ic.add_argument("json_file")
    p_ir = ing_sub.add_parser("run", help="在 api 模式下自动批量抽取")
    p_ir.add_argument("--limit", type=int, default=5)
    p_if = ing_sub.add_parser("file", help="导入外部文件或目录")
    p_if.add_argument("path")
    p_if.add_argument("--kind", default="auto", choices=["auto", "chatgpt", "gemini", "text"])

    p_search = sub.add_parser("search", help="混合检索长期记忆")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=8)
    p_search.add_argument("--private", action="store_true")

    p_prof = sub.add_parser("profile", help="查看用户画像")
    p_prof.add_argument("--full", action="store_true")
    p_prof.add_argument("--json", action="store_true")

    p_ref = sub.add_parser("reflect", help="画像定期反思")
    ref_sub = p_ref.add_subparsers(dest="reflect_action")
    ref_sub.add_parser("prepare")
    p_rp = ref_sub.add_parser("propose")
    p_rp.add_argument("json_file")
    p_ra = ref_sub.add_parser("apply")
    p_ra.add_argument("proposal_id", type=int)
    ref_sub.add_parser("run")

    p_fgt = sub.add_parser("forget", help="遗忘匹配记忆")
    p_fgt.add_argument("target")
    p_fgt.add_argument("--apply", action="store_true")

    p_exp = sub.add_parser("export", help="导出全部记忆与画像")
    p_exp.add_argument("-o", "--output", default=None)

    p_hook = sub.add_parser("hook", help="直接执行生命周期 hook")
    p_hook.add_argument("event", choices=["stop", "session-start"])

    p_inst = sub.add_parser("install-antigravity", help="安装 Antigravity 插件")
    p_inst.add_argument("--plugin-dir", default=None)
    p_inst.add_argument("--template-dir", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "hook":
        hook_argv = [args.event]
        if args.pa_home:
            hook_argv.extend(["--pa-home", args.pa_home])
        return hooks.main(hook_argv)

    handlers = {
        "init": _cmd_init,
        "doctor": _cmd_doctor,
        "scan": _cmd_scan,
        "ingest": _cmd_ingest,
        "search": _cmd_search,
        "profile": _cmd_profile,
        "reflect": _cmd_reflect,
        "forget": _cmd_forget,
        "export": _cmd_export,
        "install-antigravity": _cmd_install_antigravity,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
