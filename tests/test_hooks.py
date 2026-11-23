import json
import os
import subprocess
import sys
from pathlib import Path


def test_hooks_stop_and_session_start(tmp_path: Path) -> None:
    pa_home = tmp_path / "pa_data"
    pa_home.mkdir(parents=True)

    # 1. 测试 stop hook 追加 queue.jsonl 且返回 {}
    payload = json.dumps({"conversationId": "conv-abc", "transcriptPath": "/tmp/t.jsonl"})
    env = {**os.environ, "PA_AUTOPILOT_DISABLE": "1"}
    res_stop = subprocess.run(
        [sys.executable, "-m", "personal_assistant.hooks", "stop", "--pa-home", str(pa_home)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert json.loads(res_stop.stdout.strip()) == {}
    queue_lines = (pa_home / "queue.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(queue_lines) == 1
    assert json.loads(queue_lines[0])["conversationId"] == "conv-abc"

    # 2. 无画像时 session-start 返回 {}
    res_empty = subprocess.run(
        [sys.executable, "-m", "personal_assistant.hooks", "session-start", "--pa-home", str(pa_home)],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(res_empty.stdout.strip()) == {}

    # 3. 写入精简画像缓存后 session-start 注入 injectSteps
    (pa_home / "profile_compact.md").write_text("- 身份：开发者\n- 核心技能：Python(4/5)", encoding="utf-8")
    res_start = subprocess.run(
        [sys.executable, "-m", "personal_assistant.hooks", "session-start", "--pa-home", str(pa_home)],
        input="{}",
        text=True,
        capture_output=True,
        check=True,
    )
    parsed = json.loads(res_start.stdout.strip())
    msg = parsed["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "用户画像" in msg
    assert "Python(4/5)" in msg
