import json
from pathlib import Path

from personal_assistant.sources.antigravity import parse_antigravity_transcript
from personal_assistant.sources.chatgpt import parse_chatgpt_export


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
