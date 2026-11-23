"""向量嵌入抽象与本地模型懒加载实现。"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """嵌入模型统一协议，方便后续替换为 API 或其他向量引擎。"""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def available(self) -> bool: ...

    def embed(self, texts: list[str]) -> np.ndarray | None:
        """返回 shape=(N, dim) 的 float32 归一化向量矩阵；不可用时返回 None。"""
        ...


class NullEmbedder:
    """空嵌入器：禁用向量或模型加载失败时的降级实现，只走关键词检索。"""

    @property
    def name(self) -> str:
        return "null"

    @property
    def dim(self) -> int:
        return 0

    @property
    def available(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> np.ndarray | None:
        return None


class LocalEmbedder:
    """基于 sentence-transformers 的本地懒加载嵌入器。"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self._model_name = model_name
        self._model = None
        self._dim = 512
        self._failed = False

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return 0 if self._failed else self._dim

    @property
    def available(self) -> bool:
        return not self._failed

    def _ensure_loaded(self) -> bool:
        if self._failed:
            return False
        if self._model is not None:
            return True
        try:
            # 延迟导入以保证 MCP Server 与 Hook 启动速度
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            dim = self._model.get_sentence_embedding_dimension()
            if isinstance(dim, int) and dim > 0:
                self._dim = dim
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("本地向量模型 %s 加载失败，退化为纯关键词检索: %s", self._model_name, exc)
            self._failed = True
            return False

    def warmup(self) -> bool:
        """显式触发模型加载（用于 doctor 体检）。"""
        return self._ensure_loaded()

    def embed(self, texts: list[str]) -> np.ndarray | None:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        if not self._ensure_loaded():
            return None
        try:
            vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return np.asarray(vecs, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            logger.warning("向量编码失败，退化为纯关键词检索: %s", exc)
            self._failed = True
            return None


def get_embedder(enabled: bool = True, model_name: str = "BAAI/bge-small-zh-v1.5") -> Embedder:
    """根据配置构造嵌入器；不立即加载权重。"""
    if not enabled:
        return NullEmbedder()
    return LocalEmbedder(model_name=model_name)
