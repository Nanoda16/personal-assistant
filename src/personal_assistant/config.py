"""配置加载与路径解析模块。"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractorConfig:
    """记忆抽取器配置。"""

    mode: str = "agent"  # "agent" | "api"


@dataclass
class ApiConfig:
    """OpenAI 兼容 API 配置（仅在 extractor.mode == 'api' 时生效）。"""

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "PA_API_KEY"
    api_key: str = ""

    def resolve_api_key(self) -> str:
        """优先从环境变量读取 API Key，其次配置文件，最后在 Windows 上回退读取注册表环境变量。"""
        if self.api_key_env:
            env_val = os.environ.get(self.api_key_env, "").strip()
            if env_val:
                return env_val
        cfg_val = self.api_key.strip()
        if cfg_val:
            return cfg_val
        if self.api_key_env and sys.platform == "win32":
            try:
                import winreg

                for hive, subkey in (
                    (winreg.HKEY_CURRENT_USER, "Environment"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                ):
                    try:
                        with winreg.OpenKey(hive, subkey) as key:
                            val, _ = winreg.QueryValueEx(key, self.api_key_env)
                            text = str(val).strip()
                            if text:
                                return text
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
        return ""


@dataclass
class EmbeddingConfig:
    """本地向量模型配置。"""

    enabled: bool = True
    model: str = "BAAI/bge-small-zh-v1.5"


@dataclass
class RetrievalConfig:
    """混合检索打分权重配置。"""

    vector_weight: float = 0.45
    bm25_weight: float = 0.25
    importance_weight: float = 0.15
    recency_weight: float = 0.15
    recency_half_life_days: float = 90.0


@dataclass
class ProfileConfig:
    """用户画像渲染配置。"""

    compact_max_chars: int = 600


@dataclass
class PrivacyConfig:
    """隐私与脱敏配置。"""

    redact: bool = True


@dataclass
class SourcesConfig:
    """数据源路径配置。"""

    antigravity_brain_dir: str = "~/.gemini/antigravity/brain"


@dataclass
class BehaviorConfig:
    """助理行为与静默模式配置。"""

    silent: bool = True
    auto_apply: bool = True
    auto_ingest_limit: int = 10
    reflect_min_episodes: int = 5


@dataclass
class Config:
    """全局配置对象。"""

    pa_home: Path
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

    @property
    def db_path(self) -> Path:
        return self.pa_home / "memory.db"

    @property
    def queue_path(self) -> Path:
        return self.pa_home / "queue.jsonl"

    @property
    def compact_profile_path(self) -> Path:
        return self.pa_home / "profile_compact.md"

    @property
    def config_path(self) -> Path:
        return self.pa_home / "config.toml"

    @property
    def antigravity_brain_path(self) -> Path:
        return Path(self.sources.antigravity_brain_dir).expanduser()


def get_pa_home(override: str | Path | None = None) -> Path:
    """解析 PA_HOME 数据目录：显式参数 > 环境变量 PA_HOME > ~/.personal-assistant。"""
    if override is not None:
        return Path(override).expanduser().resolve()
    env_val = os.environ.get("PA_HOME", "").strip()
    if env_val:
        return Path(env_val).expanduser().resolve()
    return (Path.home() / ".personal-assistant").resolve()


def _merge_dataclass(cls: type[Any], data: dict[str, Any] | None) -> Any:
    if not isinstance(data, dict):
        return cls()
    valid = {f.name: data[f.name] for f in cls.__dataclass_fields__.values() if f.name in data}
    return cls(**valid)


def load_config(pa_home: str | Path | None = None) -> Config:
    """加载 PA_HOME/config.toml，缺失文件或字段时使用默认值。"""
    home = get_pa_home(pa_home)
    cfg_file = home / "config.toml"
    raw: dict[str, Any] = {}
    if cfg_file.is_file():
        with cfg_file.open("rb") as f:
            raw = tomllib.load(f)

    return Config(
        pa_home=home,
        extractor=_merge_dataclass(ExtractorConfig, raw.get("extractor")),
        api=_merge_dataclass(ApiConfig, raw.get("api")),
        embedding=_merge_dataclass(EmbeddingConfig, raw.get("embedding")),
        retrieval=_merge_dataclass(RetrievalConfig, raw.get("retrieval")),
        profile=_merge_dataclass(ProfileConfig, raw.get("profile")),
        privacy=_merge_dataclass(PrivacyConfig, raw.get("privacy")),
        sources=_merge_dataclass(SourcesConfig, raw.get("sources")),
        behavior=_merge_dataclass(BehaviorConfig, raw.get("behavior")),
    )
