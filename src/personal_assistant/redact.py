"""隐私脱敏模块：在记忆入库前抹除敏感凭据与个人证件号。"""

from __future__ import annotations

import re

# 脱敏规则表：(编译后正则, 替换文本)
_REDACT_RULES: list[tuple[re.Pattern[str], str]] = [
    # OpenAI / Anthropic 等 sk- 密钥
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED:api_key]"),
    # GitHub Personal Access Token
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[REDACTED:github_token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:github_token]"),
    # AWS Access Key ID
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    # Bearer Token
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{12,}"), "Bearer [REDACTED:token]"),
    # 英文常见密码/密钥键值对
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api_key|apikey|access_token)\s*([:=])\s*[\"']?([^\s\"']{4,})[\"']?"
        ),
        r"\1\2[REDACTED:secret]",
    ),
    # 中文密码/口令键值对
    (re.compile(r"(密码|口令)\s*([:：])\s*\S{4,}"), r"\1\2[REDACTED:secret]"),
    # 18 位中国大陆居民身份证号
    (
        re.compile(
            r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
        "[REDACTED:id_card]",
    ),
    # 中国大陆 11 位手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[REDACTED:phone]"),
]


def redact_text(text: str) -> str:
    """对输入文本执行所有脱敏规则。"""
    if not text:
        return text
    result = text
    for pattern, replacement in _REDACT_RULES:
        result = pattern.sub(replacement, result)
    return result
