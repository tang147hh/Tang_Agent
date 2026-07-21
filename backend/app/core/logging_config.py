from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import Settings, load_settings


_HANDLER_MARKER = "_tang_agent_handler"


def _already_configured(logger: logging.Logger) -> bool:
    return any(
        getattr(handler, _HANDLER_MARKER, False)
        for handler in logger.handlers
    )


def _mark_handler(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def configure_logging(settings: Settings | None = None) -> Path:
    """配置控制台日志和按日期轮转的文件日志。"""

    current = settings or load_settings()
    current.log_dir.mkdir(parents=True, exist_ok=True)

    log_path = current.log_dir / "backend.log"
    root_logger = logging.getLogger()

    if _already_configured(root_logger):
        return log_path

    level = getattr(logging, current.log_level, logging.INFO)
    root_logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console_handler = _mark_handler(logging.StreamHandler())
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = _mark_handler(
        TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger(__name__).info(
        "日志系统初始化完成：environment=%s log_path=%s",
        current.environment,
        log_path,
    )

    return log_path