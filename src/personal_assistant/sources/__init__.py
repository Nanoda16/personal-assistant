"""数据源适配器集合。"""

from personal_assistant.sources.antigravity import (
    AntigravityAdapter,
    discover_antigravity_transcripts,
    parse_antigravity_transcript,
)
from personal_assistant.sources.base import RawConversation, SourceAdapter, Turn
from personal_assistant.sources.chatgpt import parse_chatgpt_export
from personal_assistant.sources.text import parse_text_file

__all__ = [
    "AntigravityAdapter",
    "RawConversation",
    "SourceAdapter",
    "Turn",
    "discover_antigravity_transcripts",
    "parse_antigravity_transcript",
    "parse_chatgpt_export",
    "parse_text_file",
]
