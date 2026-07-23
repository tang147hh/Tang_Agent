from __future__ import annotations

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import Settings, load_settings


_HANDLER_MARKER = "_tang_agent_handler"
_HOME_DIRECTORY = str(Path.home())
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
    r"-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"
    ),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def redact_log_text(value: str) -> str:
    """Remove credentials and host-specific home paths from rendered logs."""

    sanitized = value.replace(_HOME_DIRECTORY, "~")
    sanitized = _PRIVATE_KEY_PATTERN.sub("[REDACTED]", sanitized)
    for pattern in _CREDENTIAL_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def _configured_log_path(
    logger: logging.Logger,
) -> Path | None:
    for handler in logger.handlers:
        if not getattr(handler, _HANDLER_MARKER, False):
            continue

        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename).resolve()

    return None


def _remove_configured_handlers(
    logger: logging.Logger,
) -> None:
    for handler in list(logger.handlers):
        if not getattr(handler, _HANDLER_MARKER, False):
            continue

        logger.removeHandler(handler)
        handler.close()


def _mark_handler(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def configure_logging(settings: Settings | None = None) -> Path:
    """配置控制台日志和按日期轮转的文件日志。"""

    current = settings or load_settings()
    current.log_dir.mkdir(parents=True, exist_ok=True)

    log_path = current.log_dir / "backend.log"
    root_logger = logging.getLogger()
    configured_path = _configured_log_path(root_logger)

    if configured_path == log_path.resolve():
        return log_path

    if configured_path is not None:
        _remove_configured_handlers(root_logger)

    level = getattr(logging, current.log_level, logging.INFO)
    root_logger.setLevel(level)

    formatter = RedactingFormatter(
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
        "日志系统初始化完成：environment=%s log_file=%s",
        current.environment,
        log_path.name,
    )

    return log_path
