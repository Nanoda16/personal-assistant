"""通用纯文本 / Markdown 文件解析器。"""

from __future__ import annotations

from pathlib import Path

from personal_assistant.sources.base import RawConversation, Turn


def parse_text_file(path: Path, *, source: str = "text") -> list[RawConversation]:
    """读取单个 .txt/.md 文件或目录下的所有文本文件。"""
    files: list[Path] = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(
            p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".txt", ".md"}
        )

    results: list[RawConversation] = []
    for fpath in files:
        text = fpath.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        results.append(
            RawConversation(
                source=source,
                ref=str(fpath.resolve()),
                title=fpath.stem,
                started_at="",
                turns=[Turn(role="note", text=text, step_index=1)],
                max_step=1,
            )
        )
    return results
