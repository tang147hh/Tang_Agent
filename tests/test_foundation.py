from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from app.core.config import PROJECT_ROOT, load_settings
from app.core.logging_config import configure_logging


def test_project_root() -> None:
    settings = load_settings()

    assert settings.project_root == PROJECT_ROOT
    assert settings.project_root.name == "Tang_Agent"
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.log_dir == PROJECT_ROOT / "logs"


def test_environment_variable_has_priority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    expected = tmp_path / "workspace"
    monkeypatch.setenv("TANG_AGENT_WORKSPACE_ROOT", str(expected))

    settings = load_settings()

    assert settings.workspace_root == expected.resolve()


def test_logging_creates_log_file(tmp_path: Path) -> None:
    settings = replace(
        load_settings(),
        log_dir=tmp_path / "logs",
    )

    log_path = configure_logging(settings)
    logging.getLogger("tests.foundation").info("foundation test")

    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_path.exists()
    assert "foundation test" in log_path.read_text(encoding="utf-8")


def test_logging_redacts_host_paths_credentials_and_tracebacks(
    tmp_path: Path,
) -> None:
    settings = replace(
        load_settings(),
        log_dir=tmp_path / "logs",
    )
    log_path = configure_logging(settings)
    credential = "ghp_" + "a" * 36

    try:
        raise RuntimeError(
            f"token={credential} project={PROJECT_ROOT}"
        )
    except RuntimeError:
        logging.getLogger("tests.foundation").exception(
            "provider failure in %s",
            PROJECT_ROOT,
        )

    for handler in logging.getLogger().handlers:
        handler.flush()

    content = log_path.read_text(encoding="utf-8")
    assert "Traceback" in content
    assert "[REDACTED]" in content
    assert credential not in content
    assert str(Path.home()) not in content
