from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


def _resolve_path(value: str) -> Path:
    """展开 ~ 并转换成规范绝对路径。"""

    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    """Tang Agent 的集中配置。"""

    environment: str
    project_root: Path
    data_dir: Path
    log_dir: Path
    workspace_root: Path
    log_level: str


def load_settings() -> Settings:
    """加载配置。

    优先级：
    1. 当前进程环境变量
    2. 项目 .env
    3. 代码默认值
    """

    load_dotenv(ENV_FILE, override=False)

    return Settings(
        environment=os.getenv("TANG_AGENT_ENV", "development"),
        project_root=PROJECT_ROOT,
        data_dir=_resolve_path(
            os.getenv("TANG_AGENT_DATA_DIR", str(PROJECT_ROOT / "data"))
        ),
        log_dir=_resolve_path(
            os.getenv("TANG_AGENT_LOG_DIR", str(PROJECT_ROOT / "logs"))
        ),
        workspace_root=_resolve_path(
            os.getenv(
                "TANG_AGENT_WORKSPACE_ROOT",
                str(Path.home() / "ai-workspace"),
            )
        ),
        log_level=os.getenv("TANG_AGENT_LOG_LEVEL", "INFO").upper(),
    )