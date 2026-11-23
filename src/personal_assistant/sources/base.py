"""数据源统一抽象定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Turn:
    """会话中的单轮发言。"""

    role: str  # "user" | "assistant" | "note"
    text: str
    ts: str = ""
    step_index: int = 0


@dataclass
class RawConversation:
    """标准化后的会话记录，供抽取器消费。"""

    source: str
    ref: str
    title: str
    started_at: str = ""
    turns: list[Turn] = field(default_factory=list)
    max_step: int = 0


class SourceAdapter(Protocol):
    """数据源适配器协议。"""

    source_name: str

    def load(self, path: Path, since_step: int = 0) -> RawConversation | None: ...
