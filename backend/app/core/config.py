from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    model_provider: str
    model_name: str
    model_api_key: str = field(repr=False)
    model_base_url: str
    model_temperature: float
    model_max_tokens: int
    model_timeout: float
    model_max_retries: int
    review_diff_max_files: int = 50
    review_diff_max_file_patch_chars: int = 40_000
    review_diff_max_file_changed_lines: int = 800
    review_diff_max_total_patch_chars: int = 200_000
    review_diff_max_total_changed_lines: int = 3_000
    review_git_timeout: float = 30.0
    github_review_publish_enabled: bool = False
    github_cli_timeout: float = 20.0
    github_publication_ttl_seconds: int = 900
    github_review_max_inline_comments: int = 50
    github_review_max_comment_chars: int = 8_000
    github_review_max_summary_chars: int = 20_000
    web_search_provider: str = "disabled"
    zhipu_api_key: str = field(default="", repr=False)
    web_search_cache_ttl_seconds: int = 600
    web_search_empty_cache_ttl_seconds: int = 60
    web_search_cache_max_entries: int = 128

    def __post_init__(self) -> None:
        positive = (
            "review_diff_max_files",
            "review_diff_max_file_patch_chars",
            "review_diff_max_file_changed_lines",
            "review_diff_max_total_patch_chars",
            "review_diff_max_total_changed_lines",
            "review_git_timeout",
            "github_cli_timeout",
            "github_publication_ttl_seconds",
            "github_review_max_inline_comments",
            "github_review_max_comment_chars",
            "github_review_max_summary_chars",
            "web_search_cache_ttl_seconds",
            "web_search_empty_cache_ttl_seconds",
            "web_search_cache_max_entries",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if self.web_search_provider not in {"disabled", "zhipu"}:
            raise ValueError(
                "web_search_provider 只允许 disabled 或 zhipu"
            )


def _boolean_environment(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


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
        model_provider=os.getenv(
            "TANG_AGENT_MODEL_PROVIDER",
            "deepseek",
        ).strip().lower(),
        model_name=os.getenv(
            "TANG_AGENT_MODEL_NAME",
            "deepseek-v4-pro",
        ).strip(),
        model_api_key=os.getenv(
            "TANG_AGENT_MODEL_API_KEY",
            "",
        ).strip(),
        model_base_url=os.getenv(
            "TANG_AGENT_MODEL_BASE_URL",
            "",
        ).strip(),
        model_temperature=float(
            os.getenv("TANG_AGENT_MODEL_TEMPERATURE", "0.2")
        ),
        model_max_tokens=int(
            os.getenv("TANG_AGENT_MODEL_MAX_TOKENS", "8192")
        ),
        model_timeout=float(
            os.getenv("TANG_AGENT_MODEL_TIMEOUT", "60")
        ),
        model_max_retries=int(
            os.getenv("TANG_AGENT_MODEL_MAX_RETRIES", "2")
        ),
        review_diff_max_files=int(
            os.getenv("TANG_AGENT_REVIEW_DIFF_MAX_FILES", "50")
        ),
        review_diff_max_file_patch_chars=int(
            os.getenv(
                "TANG_AGENT_REVIEW_DIFF_MAX_FILE_PATCH_CHARS",
                "40000",
            )
        ),
        review_diff_max_file_changed_lines=int(
            os.getenv(
                "TANG_AGENT_REVIEW_DIFF_MAX_FILE_CHANGED_LINES",
                "800",
            )
        ),
        review_diff_max_total_patch_chars=int(
            os.getenv(
                "TANG_AGENT_REVIEW_DIFF_MAX_TOTAL_PATCH_CHARS",
                "200000",
            )
        ),
        review_diff_max_total_changed_lines=int(
            os.getenv(
                "TANG_AGENT_REVIEW_DIFF_MAX_TOTAL_CHANGED_LINES",
                "3000",
            )
        ),
        review_git_timeout=float(
            os.getenv("TANG_AGENT_REVIEW_GIT_TIMEOUT", "30")
        ),
        github_review_publish_enabled=_boolean_environment(
            "TANG_AGENT_GITHUB_REVIEW_PUBLISH_ENABLED",
        ),
        github_cli_timeout=float(
            os.getenv("TANG_AGENT_GITHUB_CLI_TIMEOUT", "20")
        ),
        github_publication_ttl_seconds=int(
            os.getenv("TANG_AGENT_GITHUB_PUBLICATION_TTL_SECONDS", "900")
        ),
        github_review_max_inline_comments=int(
            os.getenv("TANG_AGENT_GITHUB_REVIEW_MAX_INLINE_COMMENTS", "50")
        ),
        github_review_max_comment_chars=int(
            os.getenv("TANG_AGENT_GITHUB_REVIEW_MAX_COMMENT_CHARS", "8000")
        ),
        github_review_max_summary_chars=int(
            os.getenv("TANG_AGENT_GITHUB_REVIEW_MAX_SUMMARY_CHARS", "20000")
        ),
        web_search_provider=os.getenv(
            "TANG_AGENT_WEB_SEARCH_PROVIDER",
            "disabled",
        ).strip().lower(),
        zhipu_api_key=os.getenv("ZHIPU_API_KEY", "").strip(),
        web_search_cache_ttl_seconds=int(
            os.getenv("TANG_AGENT_WEB_SEARCH_CACHE_TTL_SECONDS", "600")
        ),
        web_search_empty_cache_ttl_seconds=int(
            os.getenv("TANG_AGENT_WEB_SEARCH_EMPTY_CACHE_TTL_SECONDS", "60")
        ),
        web_search_cache_max_entries=int(
            os.getenv("TANG_AGENT_WEB_SEARCH_CACHE_MAX_ENTRIES", "128")
        ),
    )
