"""记忆抽取提示词与 OpenAI 兼容 API 抽取器。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from personal_assistant.config import ApiConfig

EXTRACTION_PROMPT = """你是一个严谨的个人记忆抽取器。请阅读下面的对话或笔记内容，从中提取关于【用户本人】的长期有效信息。

要求：
1. 只抽取关于用户的事实、偏好、技能水平与证据、工作/生活习惯、长期目标、重要经历；忽略一次性操作步骤、临时报错细节或纯工具输出。
2. 技能类记忆（type="skill"）必须附带具体证据描述（例如在什么项目中解决过什么问题）。
3. 若对话中出现疑似隐私/私人生活细节且不适合常规上下文暴露，将 sensitivity 设为 "private"，否则为 "public"。
4. 必须严格输出合法 JSON，不要附加任何解释或多余文字，格式如下：
{
  "episode": {
    "title": "简短会话标题（15字以内）",
    "summary": "1-3句话概括本次做了什么、得出什么结论",
    "outcome": "completed|in_progress|blocked|discussed"
  },
  "memories": [
    {
      "type": "fact|preference|skill|event|goal|habit",
      "content": "独立、清晰的一条长期记忆",
      "importance": 0.7,
      "confidence": 0.8,
      "sensitivity": "public",
      "profile_paths": ["skills.React", "preferences"]
    }
  ]
}
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def parse_json_response(raw: str) -> dict[str, Any]:
    """容错解析模型输出的 JSON（支持剥离 Markdown 代码块）。"""
    text = raw.strip()
    match = _FENCE_RE.match(text)
    if match:
        text = match.group(1).strip()
    # 若前后仍混杂少量非 JSON 文字，截取首尾大括号区间
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("抽取结果顶层必须是 JSON 对象")
    return data


class ApiExtractor:
    """调用 OpenAI 兼容 /chat/completions 接口的抽取器。"""

    def __init__(self, cfg: ApiConfig, timeout: float = 60.0) -> None:
        self.cfg = cfg
        self.timeout = timeout

    def complete_json(self, system_prompt: str, user_content: str, retries: int = 2) -> dict[str, Any]:
        api_key = self.cfg.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"未检测到 API Key，请设置环境变量 {self.cfg.api_key_env} 或在 config.toml 配置 api.api_key"
            )

        base = self.cfg.base_url.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        last_err: Exception | None = None
        for _ in range(retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                content = body["choices"][0]["message"]["content"]
                return parse_json_response(content)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        raise RuntimeError(f"API 抽取失败（已重试 {retries} 次）: {last_err}")

    def extract(self, conversation_text: str) -> dict[str, Any]:
        return self.complete_json(EXTRACTION_PROMPT, conversation_text)
