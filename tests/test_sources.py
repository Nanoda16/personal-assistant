import json
from pathlib import Path

from personal_assistant import ingest
from personal_assistant.config import load_config
from personal_assistant.sources.antigravity import parse_antigravity_transcript
from personal_assistant.sources.chatgpt import parse_chatgpt_export
from personal_assistant.sources.gemini import parse_gemini_takeout
from personal_assistant.store import MemoryStore


def test_antigravity_parser_strip_subagent_and_incremental(tmp_path: Path) -> None:
    main_log = tmp_path / "main.jsonl"
    rows = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-11-23T06:00:00Z",
            "content": "<USER_REQUEST>\n帮我整理 DocQA 前端\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime: 123\n</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-11-23T06:00:10Z",
            "thinking": "internal thinking should be dropped",
            "content": "好的，我先检查前端目录。",
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "created_at": "2026-11-23T06:00:12Z",
            "content": "Command output noise",
        },
        {
            "step_index": 3,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-11-23T06:01:00Z",
            "content": "<USER_REQUEST>\n加上暗黑模式\n</USER_REQUEST>",
        },
    ]
    main_log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    conv = parse_antigravity_transcript(main_log, ref="conv-1")
    assert conv is not None
    assert len(conv.turns) == 3
    assert conv.turns[0].text == "帮我整理 DocQA 前端"
    assert "ADDITIONAL_METADATA" not in conv.turns[0].text
    assert "internal thinking" not in conv.turns[1].text
    assert conv.max_step == 3

    # 增量读取 since_step=1 只返回 step 3
    inc = parse_antigravity_transcript(main_log, ref="conv-1", since_step=1)
    assert inc is not None
    assert len(inc.turns) == 1
    assert inc.turns[0].text == "加上暗黑模式"

    # 子代理会话（无 USER_EXPLICIT）应被跳过返回 None
    sub_log = tmp_path / "sub.jsonl"
    sub_rows = [
        {"step_index": 0, "source": "SYSTEM", "type": "USER_INPUT", "content": "subagent prompt"},
        {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "done"},
    ]
    sub_log.write_text("\n".join(json.dumps(r) for r in sub_rows), encoding="utf-8")
    assert parse_antigravity_transcript(sub_log, ref="sub-1") is None


def test_chatgpt_export_parser(tmp_path: Path) -> None:
    export_file = tmp_path / "conversations.json"
    sample = [
        {
            "id": "c1",
            "title": "Python 学习",
            "create_time": 1700000000,
            "current_node": "n2",
            "mapping": {
                "root": {"id": "root", "parent": None, "message": None},
                "n1": {
                    "id": "n1",
                    "parent": "root",
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["怎么用 pytest?"]},
                        "create_time": 1700000001,
                    },
                },
                "n2": {
                    "id": "n2",
                    "parent": "n1",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["先安装 pytest，然后编写 test_*.py。"]},
                        "create_time": 1700000005,
                    },
                },
            },
        }
    ]
    export_file.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")
    convs = parse_chatgpt_export(export_file)
    assert len(convs) == 1
    assert convs[0].title == "Python 学习"
    assert len(convs[0].turns) == 2


def test_parse_gemini_takeout_groups_orders_and_filters(tmp_path: Path) -> None:
    takeout_dir = tmp_path / "Gemini Apps"
    takeout_dir.mkdir()
    activity_file = takeout_dir / "我的活动记录.json"
    # Takeout 默认按时间倒序排列
    sample = [
        {
            "header": "Gemini Apps",
            "title": "Prompted 帮我加一个暗黑模式",
            "time": "2026-11-23T10:05:00.000Z",
            "products": ["Gemini Apps"],
            "details": [{"url": "https://gemini.google.com/app/convabc123"}],
            "safeHtmlItem": [{"html": "<p>好的，可以使用 <b>CSS 变量</b> 实现。</p>"}],
        },
        {
            "header": "Gemini Apps",
            "title": "Cleared previous feedback",
            "time": "2026-11-23T10:04:00.000Z",
            "products": ["Gemini Apps"],
        },
        {
            "header": "Gemini Apps",
            "title": "Created Gemini Canvas titled Demo",
            "time": "2026-11-23T10:03:00.000Z",
            "products": ["Gemini Apps"],
            "subtitles": [{"name": "<html>...</html>"}],
        },
        {
            "header": "Gemini Apps",
            "title": "Prompted 我正在用 React 19 重构 DocQA 前端",
            "time": "2026-11-23T10:00:00.000Z",
            "products": ["Gemini Apps"],
            "details": [{"url": "https://gemini.google.com/app/convabc123"}],
            "safeHtmlItem": [{"html": "<p>收到，React 19 很适合这个场景。</p>"}],
        },
        {
            "header": "Gemini Apps",
            "title": "Prompted 零散提问没有 URL",
            "time": "2026-11-20T08:00:00.000Z",
            "products": ["Gemini Apps"],
            "safeHtmlItem": [{"html": "<p>已记录。</p>"}],
        },
    ]
    activity_file.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    # 目录自动识别
    convs = parse_gemini_takeout(takeout_dir)
    assert len(convs) == 2
    # 按 started_at 升序：2026-11-20 在前，2026-11-23 在后
    assert convs[0].ref == "gemini:date:2026-11-20"
    assert convs[0].turns[0].text == "零散提问没有 URL"

    main_conv = convs[1]
    assert main_conv.source == "gemini"
    assert main_conv.ref == "gemini:convabc123"
    assert main_conv.title == "我正在用 React 19 重构 DocQA 前端"
    assert len(main_conv.turns) == 4
    # 验证组内已按时间升序排列且剥离了 Prompted 与 HTML 标签
    assert main_conv.turns[0].role == "user"
    assert main_conv.turns[0].text == "我正在用 React 19 重构 DocQA 前端"
    assert main_conv.turns[1].role == "assistant"
    assert main_conv.turns[1].text == "收到，React 19 很适合这个场景。"
    assert main_conv.turns[2].text == "帮我加一个暗黑模式"
    assert main_conv.turns[3].text == "好的，可以使用 CSS 变量 实现。"


def test_ingest_file_auto_detects_gemini_takeout(tmp_path: Path) -> None:
    pa_home = tmp_path / "pa_home"
    pa_home.mkdir()
    cfg = load_config(pa_home)
    store = MemoryStore(cfg.db_path, redact_enabled=True)

    takeout_dir = tmp_path / "Gemini Apps"
    takeout_dir.mkdir()
    activity_file = takeout_dir / "我的活动记录.json"
    sample = [
        {
            "header": "Gemini Apps",
            "title": "Prompted 测试自动识别",
            "time": "2026-11-23T12:00:00.000Z",
            "products": ["Gemini Apps"],
            "details": [{"url": "https://gemini.google.com/app/xyz789"}],
            "safeHtmlItem": [{"html": "<p>测试成功。</p>"}],
        }
    ]
    activity_file.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    res = ingest.ingest_file(store, cfg, takeout_dir, kind="auto")
    assert res["ok"] is True
    assert res["queued"] == 1

    nxt = ingest.next_item(store)
    assert nxt is not None
    assert nxt["source"] == "gemini"
    assert nxt["ref"] == "gemini:xyz789"
    assert "测试自动识别" in nxt["text"]
    assert "测试成功。" in nxt["text"]
